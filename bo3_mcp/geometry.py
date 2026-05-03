"""High-level geometry helpers — rooms, walls, slabs, stairs.

All operate on the top-level `<map>.map`'s worldspawn entity (where static
geometry lives in BO3 maps). Brushes are SOLID; to make a hollow room you
surround a void with 6 wall brushes (`carve_room`)."""

from __future__ import annotations

from typing import Literal

from . import brushes, mapfile, paths

Point = tuple[float, float, float]
Side = Literal["bottom", "top", "south", "north", "west", "east"]


def _load_top(map_name: str) -> tuple[mapfile.MapFile, mapfile.Entity]:
    target = paths.map_source(map_name)
    if not target.exists():
        raise FileNotFoundError(
            f"top-level map missing: {target}. Run scaffold_zombie_map first."
        )
    mf = mapfile.load(target)
    ws = mf.worldspawn
    if ws is None:
        raise ValueError(f"no worldspawn entity in {target}")
    return mf, ws


def _save_top(mf: mapfile.MapFile, map_name: str) -> None:
    mf.save(paths.map_source(map_name))


# --- Single-brush helpers --------------------------------------------------


def add_box_brush(
    map_name: str,
    mins: Point,
    maxs: Point,
    texture: str = brushes.DEFAULT_TEXTURE,
    *,
    face_textures: dict[Side, str] | None = None,
) -> dict:
    """Add a single solid axis-aligned box brush to the worldspawn entity.

    Useful as a primitive for ramps, debris, pillars, anything ad-hoc."""
    mins, maxs = brushes.normalize_box(mins, maxs)
    mf, ws = _load_top(map_name)
    ws.brushes.append(
        brushes.box_brush(mins, maxs, texture, face_textures=face_textures)
    )
    _save_top(mf, map_name)
    return {
        "target": "worldspawn",
        "mins": mins,
        "maxs": maxs,
        "texture": texture,
        "brushes_added": 1,
        "worldspawn_total_brushes": len(ws.brushes),
    }


def add_floor(
    map_name: str,
    mins: Point,
    maxs_xy: tuple[float, float],
    *,
    thickness: float = 16.0,
    texture: str = brushes.DEFAULT_TEXTURE,
) -> dict:
    """Add a floor slab. `mins` is the bottom-left-front corner; `maxs_xy` is
    the (max_x, max_y); the slab extends upward by `thickness`."""
    x0, y0, z0 = mins
    x1, y1 = maxs_xy
    return add_box_brush(map_name, (x0, y0, z0), (x1, y1, z0 + thickness), texture)


def add_wall(
    map_name: str,
    mins: Point,
    maxs: Point,
    texture: str = brushes.DEFAULT_TEXTURE,
) -> dict:
    """Alias for add_box_brush — semantic helper for wall placements."""
    return add_box_brush(map_name, mins, maxs, texture)


# --- Room (hollow box) -----------------------------------------------------


def carve_room(
    map_name: str,
    mins: Point,
    maxs: Point,
    *,
    wall_thickness: float = 16.0,
    texture: str = brushes.DEFAULT_TEXTURE,
    floor_texture: str | None = None,
    ceiling_texture: str | None = None,
) -> dict:
    """Build a hollow rectangular room: 6 wall brushes (floor, ceiling, four
    walls) surrounding a void. The floor/ceiling slabs span the full footprint;
    the four walls span the height between them. They overlap at corners — the
    BSP compiler unifies the geometry.

    The void interior is `(mins + wall_thickness) -> (maxs - wall_thickness)` on
    each axis. Returns the void bounds so the caller knows where it's safe to
    place entities (perks, spawn points, etc.) inside the room."""
    mins, maxs = brushes.normalize_box(mins, maxs)
    x0, y0, z0 = mins
    x1, y1, z1 = maxs
    t = wall_thickness

    if x1 - x0 <= 2 * t or y1 - y0 <= 2 * t or z1 - z0 <= 2 * t:
        raise ValueError(
            f"room is smaller than 2 * wall_thickness ({2 * t}) on some axis; "
            f"void would be empty. mins={mins} maxs={maxs} t={t}"
        )

    floor_tex = floor_texture or texture
    ceil_tex = ceiling_texture or texture

    walls: list[tuple[Point, Point, str]] = [
        # Floor: full footprint slab at bottom
        ((x0, y0, z0), (x1, y1, z0 + t), floor_tex),
        # Ceiling: full footprint slab at top
        ((x0, y0, z1 - t), (x1, y1, z1), ceil_tex),
        # South wall (y = y0..y0+t), height between floor and ceiling
        ((x0, y0, z0 + t), (x1, y0 + t, z1 - t), texture),
        # North wall (y = y1-t..y1)
        ((x0, y1 - t, z0 + t), (x1, y1, z1 - t), texture),
        # West wall (x = x0..x0+t), inside the south/north walls
        ((x0, y0 + t, z0 + t), (x0 + t, y1 - t, z1 - t), texture),
        # East wall (x = x1-t..x1)
        ((x1 - t, y0 + t, z0 + t), (x1, y1 - t, z1 - t), texture),
    ]

    mf, ws = _load_top(map_name)
    for wall_mins, wall_maxs, wall_tex in walls:
        ws.brushes.append(brushes.box_brush(wall_mins, wall_maxs, wall_tex))
    _save_top(mf, map_name)

    return {
        "outer_mins": mins,
        "outer_maxs": maxs,
        "void_mins": (x0 + t, y0 + t, z0 + t),
        "void_maxs": (x1 - t, y1 - t, z1 - t),
        "interior_size": (x1 - x0 - 2 * t, y1 - y0 - 2 * t, z1 - z0 - 2 * t),
        "wall_thickness": t,
        "brushes_added": len(walls),
        "worldspawn_total_brushes": len(ws.brushes),
    }


# --- Room with openings ----------------------------------------------------


def carve_room_with_openings(
    map_name: str,
    mins: Point,
    maxs: Point,
    openings: list[dict],
    *,
    wall_thickness: float = 16.0,
    texture: str = brushes.DEFAULT_TEXTURE,
    floor_texture: str | None = None,
    ceiling_texture: str | None = None,
) -> dict:
    """Hollow rectangular room with pre-cut openings (doorways or windows) in
    one call. Each opening is a dict:

      {"side": "south"|"north"|"east"|"west",
       "width": <wall-axis extent>,
       "height": <z extent>,
       "center_offset": <shift along wall axis from wall center, default 0>,
       "bottom": <z above the inside floor, default 0 = doorway on floor>}

    For windows, set `bottom` > 0. Multiple openings per side are supported
    (v0.8+) — the wall is decomposed into a left fill, between-opening fills,
    a right fill, plus above and below sub-walls per opening."""
    mins, maxs = brushes.normalize_box(mins, maxs)
    x0, y0, z0 = mins
    x1, y1, z1 = maxs
    t = wall_thickness

    if x1 - x0 <= 2 * t or y1 - y0 <= 2 * t or z1 - z0 <= 2 * t:
        raise ValueError(
            f"room is smaller than 2 * wall_thickness ({2 * t}) on some axis"
        )

    # Group openings by side. Multiple per side are supported.
    by_side: dict[str, list[dict]] = {}
    for op in openings:
        side = op.get("side")
        if side not in ("south", "north", "east", "west"):
            raise ValueError(f"opening 'side' must be south/north/east/west; got {side!r}")
        by_side.setdefault(side, []).append(op)

    floor_tex = floor_texture or texture
    ceil_tex = ceiling_texture or texture

    # Per-face texturing: only the face pointing INTO the room interior gets
    # the visible material. All other 5 faces use `caulk` (non-rendering).
    # This matches zm_giant's wall-brush convention. Without it, BO3's BSP
    # compiler appears to mark the interior face as nodraw when both sides
    # of a brush face have the same visible material — resulting in walls
    # appearing as the skybox texture from inside the room (the "void bug").
    #
    # For each brush type, the interior-facing side:
    #   floor: top      (texture on top, caulk on bottom + 4 sides)
    #   ceiling: bottom (texture on bottom, caulk on top + 4 sides)
    #   south wall: north (texture on +Y face, caulk on -Y + 4 sides)
    #   north wall: south
    #   west wall: east
    #   east wall: west
    INTERIOR_FACE_FOR_SIDE: dict[str, Side] = {
        "south": "north",  # south wall's north face points into room
        "north": "south",
        "west":  "east",
        "east":  "west",
    }

    def _face_textures_with_only(visible_face: Side, visible_tex: str) -> dict[Side, str]:
        sides_all: list[Side] = ["bottom", "top", "south", "north", "west", "east"]
        return {s: ("caulk" if s != visible_face else visible_tex) for s in sides_all}

    floor_faces = _face_textures_with_only("top", floor_tex)
    ceil_faces = _face_textures_with_only("bottom", ceil_tex)

    # Floor + ceiling: full footprint slabs
    slabs: list[tuple[Point, Point, dict[Side, str]]] = [
        ((x0, y0, z0), (x1, y1, z0 + t), floor_faces),
        ((x0, y0, z1 - t), (x1, y1, z1), ceil_faces),
    ]

    # Wall extents (the box for each wall; opening cuts a hole in this).
    wall_specs: dict[str, Box] = {
        "south": ((x0, y0, z0 + t), (x1, y0 + t, z1 - t)),
        "north": ((x0, y1 - t, z0 + t), (x1, y1, z1 - t)),
        "west":  ((x0, y0 + t, z0 + t), (x0 + t, y1 - t, z1 - t)),
        "east":  ((x1 - t, y0 + t, z0 + t), (x1, y1 - t, z1 - t)),
    }

    brush_specs: list[tuple[Point, Point, dict[Side, str]]] = list(slabs)
    sub_wall_summary: list[dict] = []

    # All-faces-visible texturing (used for sub-walls around openings — their
    # opening-facing faces also need to be visible, since the player sees them
    # walking through the doorway).
    all_visible_faces: dict[Side, str] = {
        s: texture for s in ["bottom", "top", "south", "north", "west", "east"]
    }

    for side, (w_mins, w_maxs) in wall_specs.items():
        wall_faces = _face_textures_with_only(INTERIOR_FACE_FOR_SIDE[side], texture)
        if side not in by_side:
            # Full wall (no opening) — caulk all but the interior face.
            brush_specs.append((w_mins, w_maxs, wall_faces))
            continue

        opening_bounds = [
            _opening_bounds_for_side(side, w_mins, w_maxs, op)
            for op in by_side[side]
        ]
        sub_walls = _split_wall_with_openings(side, w_mins, w_maxs, opening_bounds)
        sub_walls = [
            (mn, mx) for (mn, mx) in sub_walls
            if mx[0] > mn[0] and mx[1] > mn[1] and mx[2] > mn[2]
        ]
        for sm, sM in sub_walls:
            # Sub-walls (lintels/sills/side-walls around openings) need visible
            # faces on their opening-facing side too — caulking them makes the
            # player see the sky texture when walking through the doorway
            # (the "white streak in the doorway" bug). Use full visible
            # texturing for these.
            brush_specs.append((sm, sM, all_visible_faces))
        sub_wall_summary.append({
            "side": side,
            "openings": len(opening_bounds),
            "sub_walls": len(sub_walls),
        })

    mf, ws = _load_top(map_name)
    for bm, bM, faces in brush_specs:
        # box_brush takes a default texture (used as fallback) + face_textures override
        ws.brushes.append(brushes.box_brush(bm, bM, "caulk", face_textures=faces))
    _save_top(mf, map_name)

    return {
        "outer_mins": mins,
        "outer_maxs": maxs,
        "void_mins": (x0 + t, y0 + t, z0 + t),
        "void_maxs": (x1 - t, y1 - t, z1 - t),
        "interior_size": (x1 - x0 - 2 * t, y1 - y0 - 2 * t, z1 - z0 - 2 * t),
        "wall_thickness": t,
        "openings": sub_wall_summary,
        "brushes_added": len(brush_specs),
        "worldspawn_total_brushes": len(ws.brushes),
    }


def _split_wall_with_openings(
    side: str,
    w_mins: Point,
    w_maxs: Point,
    openings: list[tuple[Point, Point]],
) -> list[tuple[Point, Point]]:
    """Decompose a wall with N openings into sub-walls.

    Sweep the openings along the wall's W axis; build:
      - left fill (full height, from wall start to first opening start)
      - per-opening: above strip + below strip
      - between-opening fills (full height, between consecutive openings)
      - right fill (full height, from last opening end to wall end)

    Overlapping openings are tolerated (the BSP compiler unifies coincident
    brushes); if you want clean output, don't pass overlapping openings."""
    if not openings:
        return [(w_mins, w_maxs)]

    wx0, wy0, wz0 = w_mins
    wx1, wy1, wz1 = w_maxs

    is_y_thin = side in ("south", "north")  # wall thickness is on Y axis
    sub: list[tuple[Point, Point]] = []

    if is_y_thin:
        # Wall axis is X, height axis is Z.
        ops = sorted(openings, key=lambda mm: mm[0][0])
        cursor_w = wx0
        for (om, oM) in ops:
            ox_a, ox_b = om[0], oM[0]
            oz_a, oz_b = om[2], oM[2]
            # Left/between fill: full height from cursor to opening start
            if ox_a > cursor_w:
                sub.append(((cursor_w, wy0, wz0), (ox_a, wy1, wz1)))
            # Above and below the opening (within the opening's W range)
            sub.append(((ox_a, wy0, wz0), (ox_b, wy1, oz_a)))   # below
            sub.append(((ox_a, wy0, oz_b), (ox_b, wy1, wz1)))   # above
            cursor_w = max(cursor_w, ox_b)
        # Right fill
        if cursor_w < wx1:
            sub.append(((cursor_w, wy0, wz0), (wx1, wy1, wz1)))
    else:
        # Wall thin on X. Wall axis is Y, height axis is Z.
        ops = sorted(openings, key=lambda mm: mm[0][1])
        cursor_w = wy0
        for (om, oM) in ops:
            oy_a, oy_b = om[1], oM[1]
            oz_a, oz_b = om[2], oM[2]
            if oy_a > cursor_w:
                sub.append(((wx0, cursor_w, wz0), (wx1, oy_a, wz1)))
            sub.append(((wx0, oy_a, wz0), (wx1, oy_b, oz_a)))
            sub.append(((wx0, oy_a, oz_b), (wx1, oy_b, wz1)))
            cursor_w = max(cursor_w, oy_b)
        if cursor_w < wy1:
            sub.append(((wx0, cursor_w, wz0), (wx1, wy1, wz1)))

    return sub


def _opening_bounds_for_side(
    side: str, w_mins: Point, w_maxs: Point, op: dict,
) -> tuple[Point, Point]:
    """Translate an opening spec dict into 3D mins/maxs intersecting the wall."""
    width = float(op.get("width", 64.0))
    height = float(op.get("height", 96.0))
    offset = float(op.get("center_offset", 0.0))
    bottom = float(op.get("bottom", 0.0))

    wx0, wy0, wz0 = w_mins
    wx1, wy1, wz1 = w_maxs
    wall_z_extent = wz1 - wz0

    if bottom < 0 or bottom + height > wall_z_extent:
        raise ValueError(
            f"opening (bottom={bottom}, height={height}) doesn't fit within "
            f"wall z range [{wz0}, {wz1}] (wall height = {wall_z_extent})"
        )

    z_min = wz0 + bottom
    z_max = wz0 + bottom + height

    if side in ("south", "north"):
        # Y-thin wall; opening varies in (x, z).
        wall_x_extent = wx1 - wx0
        if width > wall_x_extent:
            raise ValueError(
                f"opening width {width} exceeds wall x extent {wall_x_extent}"
            )
        x_center = (wx0 + wx1) / 2 + offset
        x_min = x_center - width / 2
        x_max = x_center + width / 2
        if x_min < wx0 or x_max > wx1:
            raise ValueError(
                f"opening (width={width}, offset={offset}) extends past wall "
                f"x bounds [{wx0}, {wx1}] — center_offset is too large"
            )
        return ((x_min, wy0, z_min), (x_max, wy1, z_max))
    else:
        # X-thin wall; opening varies in (y, z).
        wall_y_extent = wy1 - wy0
        if width > wall_y_extent:
            raise ValueError(
                f"opening width {width} exceeds wall y extent {wall_y_extent}"
            )
        y_center = (wy0 + wy1) / 2 + offset
        y_min = y_center - width / 2
        y_max = y_center + width / 2
        if y_min < wy0 or y_max > wy1:
            raise ValueError(
                f"opening (width={width}, offset={offset}) extends past wall "
                f"y bounds [{wy0}, {wy1}] — center_offset is too large"
            )
        return ((wx0, y_min, z_min), (wx1, y_max, z_max))


# --- Stairs ----------------------------------------------------------------


def add_stairs(
    map_name: str,
    base_origin: Point,
    *,
    step_count: int,
    step_depth: float = 16.0,
    step_height: float = 8.0,
    step_width: float = 96.0,
    direction: Literal["+x", "-x", "+y", "-y"] = "+y",
    texture: str = brushes.DEFAULT_TEXTURE,
) -> dict:
    """Generate a flight of stairs as `step_count` stacked box brushes.

    `base_origin` is the bottom-front corner of the first step. Stairs ascend
    in `direction` (+y by default), with each step rising `step_height` and
    extending `step_depth` beyond the previous step's front edge. `step_width`
    is the perpendicular extent."""
    if step_count < 1:
        raise ValueError("step_count must be >= 1")
    if direction not in ("+x", "-x", "+y", "-y"):
        raise ValueError(f"direction must be one of +x, -x, +y, -y; got {direction!r}")

    bx, by, bz = base_origin
    mf, ws = _load_top(map_name)

    steps_added: list[tuple[Point, Point]] = []

    for i in range(step_count):
        # Each step is taller and deeper than the last (it includes the volume
        # under all preceding steps so it acts as a solid riser).
        step_top = bz + (i + 1) * step_height

        if direction == "+y":
            mins = (bx, by, bz)
            maxs = (bx + step_width, by + (i + 1) * step_depth, step_top)
        elif direction == "-y":
            mins = (bx, by - (i + 1) * step_depth, bz)
            maxs = (bx + step_width, by, step_top)
        elif direction == "+x":
            mins = (bx, by, bz)
            maxs = (bx + (i + 1) * step_depth, by + step_width, step_top)
        else:  # "-x"
            mins = (bx - (i + 1) * step_depth, by, bz)
            maxs = (bx, by + step_width, step_top)

        ws.brushes.append(brushes.box_brush(mins, maxs, texture))
        steps_added.append((mins, maxs))

    _save_top(mf, map_name)
    return {
        "step_count": step_count,
        "direction": direction,
        "first_step_mins": steps_added[0][0],
        "last_step_maxs": steps_added[-1][1],
        "brushes_added": step_count,
        "worldspawn_total_brushes": len(ws.brushes),
    }


# --- Map-wide exterior shell -----------------------------------------------


def seal_exterior(
    map_name: str,
    mins: Point,
    maxs: Point,
    *,
    buffer: float = 128.0,
    shell_thickness: float = 32.0,
    texture: str = "sky",
) -> dict:
    """Wrap the playable area in a 6-slab "skybox shell" to seal the BSP
    AND mark the world boundary for BO3's renderer.

    REQUIRED for ALL custom maps, not just maps with barricades. Without
    a skybox shell, BO3's BSP compiler doesn't know where the world ends
    and renders any wall that touches the void as the SKY texture. From
    inside the room, this manifests as "the walls are sky" — you see
    through them to the skybox and the entire room appears as void/clouds.
    (Diagnosed via `cod2map64` error: "When an Umbra brush is present the
    map must be sealed by an outer skybox.")

    Default texture is now `sky` (was `caulk`). Sky is solid for collision
    AND marks the world boundary; caulk is solid+invisible but doesn't
    establish the boundary. Use `caulk` only if you have an outer sky shell
    elsewhere.

    `mins`, `maxs` are the extents of your playable area (outermost bounds
    of all rooms). `buffer` is the gap between the playable area and the
    shell — barricades extend ~64 units past their wall, so 128 is a safe
    default. `shell_thickness` is how thick each shell slab is.

    Adds 6 box brushes to worldspawn. Call AFTER all rooms/barricades are
    placed, BEFORE compiling."""
    mins, maxs = brushes.normalize_box(mins, maxs)
    x0, y0, z0 = mins
    x1, y1, z1 = maxs

    # Inner empty-region bounds (playable + buffer)
    ix0 = x0 - buffer
    iy0 = y0 - buffer
    iz0 = z0 - buffer
    ix1 = x1 + buffer
    iy1 = y1 + buffer
    iz1 = z1 + buffer

    # Outer shell bounds (inner + shell_thickness on each side)
    ox0 = ix0 - shell_thickness
    oy0 = iy0 - shell_thickness
    oz0 = iz0 - shell_thickness
    ox1 = ix1 + shell_thickness
    oy1 = iy1 + shell_thickness
    oz1 = iz1 + shell_thickness

    # Six slabs that together enclose the playable + buffer region
    slabs: list[tuple[Point, Point]] = [
        # Bottom and top — full xy footprint
        ((ox0, oy0, oz0), (ox1, oy1, iz0)),
        ((ox0, oy0, iz1), (ox1, oy1, oz1)),
        # South and north — full x, but only between inner top/bottom
        ((ox0, oy0, iz0), (ox1, iy0, iz1)),
        ((ox0, iy1, iz0), (ox1, oy1, iz1)),
        # West and east — only between south/north and top/bottom
        ((ox0, iy0, iz0), (ix0, iy1, iz1)),
        ((ix1, iy0, iz0), (ox1, iy1, iz1)),
    ]

    mf, ws = _load_top(map_name)
    for slab_mins, slab_maxs in slabs:
        ws.brushes.append(brushes.box_brush(slab_mins, slab_maxs, texture))
    _save_top(mf, map_name)

    return {
        "playable_mins": mins,
        "playable_maxs": maxs,
        "buffer": buffer,
        "shell_thickness": shell_thickness,
        "shell_outer_mins": (ox0, oy0, oz0),
        "shell_outer_maxs": (ox1, oy1, oz1),
        "slabs_added": len(slabs),
        "worldspawn_total_brushes": len(ws.brushes),
    }


# --- Doorway carving -------------------------------------------------------


def add_doorway_to_wall(
    map_name: str,
    wall_mins: Point,
    wall_maxs: Point,
    opening_mins: Point,
    opening_maxs: Point,
    texture: str = brushes.DEFAULT_TEXTURE,
) -> dict:
    """Build a wall with a rectangular hole in it. Replaces what a single
    `add_wall` would have produced with up to 4 sub-brushes (above, below,
    left, right of the opening) so an entity can pass through.

    Pass the FULL wall extents (as if there were no opening) plus the desired
    opening rectangle. The opening must be entirely within the wall and one of
    its dimensions must match the wall (the wall's "thickness" axis)."""
    wall_mins, wall_maxs = brushes.normalize_box(wall_mins, wall_maxs)
    opening_mins, opening_maxs = brushes.normalize_box(opening_mins, opening_maxs)

    wx0, wy0, wz0 = wall_mins
    wx1, wy1, wz1 = wall_maxs
    ox0, oy0, oz0 = opening_mins
    ox1, oy1, oz1 = opening_maxs

    # Detect wall orientation by which axis has the smallest extent.
    extents = (wx1 - wx0, wy1 - wy0, wz1 - wz0)
    thin_axis = min(range(3), key=lambda i: extents[i])

    if thin_axis == 0:
        # X-thin wall: opening varies in (y, z); x is shared with wall thickness
        if not (ox0 >= wx0 and ox1 <= wx1):
            raise ValueError("opening x range must be within wall x range")
        sub_walls = _split_wall_yz(wall_mins, wall_maxs, opening_mins, opening_maxs)
    elif thin_axis == 1:
        # Y-thin wall: opening varies in (x, z)
        if not (oy0 >= wy0 and oy1 <= wy1):
            raise ValueError("opening y range must be within wall y range")
        sub_walls = _split_wall_xz(wall_mins, wall_maxs, opening_mins, opening_maxs)
    else:
        # Z-thin wall (a slab) — unusual but supported
        sub_walls = _split_wall_xy(wall_mins, wall_maxs, opening_mins, opening_maxs)

    # Filter degenerate sub-walls (zero or negative size on any axis)
    sub_walls = [
        (mn, mx) for (mn, mx) in sub_walls
        if mx[0] > mn[0] and mx[1] > mn[1] and mx[2] > mn[2]
    ]

    mf, ws = _load_top(map_name)
    for wm, wM in sub_walls:
        ws.brushes.append(brushes.box_brush(wm, wM, texture))
    _save_top(mf, map_name)

    return {
        "wall_mins": wall_mins,
        "wall_maxs": wall_maxs,
        "opening_mins": opening_mins,
        "opening_maxs": opening_maxs,
        "sub_walls": [{"mins": list(m), "maxs": list(M)} for m, M in sub_walls],
        "brushes_added": len(sub_walls),
        "worldspawn_total_brushes": len(ws.brushes),
    }


def _split_wall_yz(
    wall_mins: Point, wall_maxs: Point,
    opening_mins: Point, opening_maxs: Point,
) -> list[tuple[Point, Point]]:
    """Split a wall whose thickness is on X. Opening is in (y, z)."""
    wx0, wy0, wz0 = wall_mins
    wx1, wy1, wz1 = wall_maxs
    _, oy0, oz0 = opening_mins
    _, oy1, oz1 = opening_maxs
    return [
        ((wx0, wy0, wz0), (wx1, oy0, wz1)),  # left of opening (y < oy0)
        ((wx0, oy1, wz0), (wx1, wy1, wz1)),  # right of opening (y > oy1)
        ((wx0, oy0, wz0), (wx1, oy1, oz0)),  # below opening (within y span)
        ((wx0, oy0, oz1), (wx1, oy1, wz1)),  # above opening
    ]


def _split_wall_xz(
    wall_mins: Point, wall_maxs: Point,
    opening_mins: Point, opening_maxs: Point,
) -> list[tuple[Point, Point]]:
    """Split a wall whose thickness is on Y. Opening is in (x, z)."""
    wx0, wy0, wz0 = wall_mins
    wx1, wy1, wz1 = wall_maxs
    ox0, _, oz0 = opening_mins
    ox1, _, oz1 = opening_maxs
    return [
        ((wx0, wy0, wz0), (ox0, wy1, wz1)),  # left
        ((ox1, wy0, wz0), (wx1, wy1, wz1)),  # right
        ((ox0, wy0, wz0), (ox1, wy1, oz0)),  # below
        ((ox0, wy0, oz1), (ox1, wy1, wz1)),  # above
    ]


def _split_wall_xy(
    wall_mins: Point, wall_maxs: Point,
    opening_mins: Point, opening_maxs: Point,
) -> list[tuple[Point, Point]]:
    """Split a slab whose thickness is on Z. Opening is a hole in the slab."""
    wx0, wy0, wz0 = wall_mins
    wx1, wy1, wz1 = wall_maxs
    ox0, oy0, _ = opening_mins
    ox1, oy1, _ = opening_maxs
    return [
        ((wx0, wy0, wz0), (ox0, wy1, wz1)),
        ((ox1, wy0, wz0), (wx1, wy1, wz1)),
        ((ox0, wy0, wz0), (ox1, oy0, wz1)),
        ((ox0, oy1, wz0), (ox1, wy1, wz1)),
    ]
