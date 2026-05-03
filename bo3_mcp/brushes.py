"""Synthesize axis-aligned box brushes in Treyarch's `iwmap 4` brush format.

Convention note: Treyarch's brush format uses **inward-normal winding** —
the cross product `(B - A) x (C - A)` of the three face points points INTO
the brush solid, not out. This is opposite to the standard Quake convention.
Verified by dissecting brush 0 of `_prefabs/zm/zm_core/buyable_magic_box_start.map`
on 2026-04-30: the +X face's points produce a -X cross product.
"""

from __future__ import annotations

from typing import Literal

from . import mapfile

Point = tuple[float, float, float]
Box = tuple[Point, Point]  # (mins, maxs)
Side = Literal["bottom", "top", "south", "north", "west", "east"]

# Default texture/lightmap face params (matches the cleanest brushes in zm_giant).
_FACE_PARAMS = "64 64 0 0 0 0 lightmap_gray 16384 16384 0 0 0 0"

DEFAULT_TEXTURE = "t7_concrete_trowelled"


def box_brush(
    mins: Point,
    maxs: Point,
    texture: str = DEFAULT_TEXTURE,
    *,
    face_textures: dict[Side, str] | None = None,
) -> str:
    """Generate the brush body text (`{ ... }`) for an axis-aligned box.

    Returns text suitable for appending to `Entity.brushes`. Caller does not
    include a `// brush N` header — the serializer renumbers and writes that.
    """
    x0, y0, z0 = mins
    x1, y1, z1 = maxs
    if x1 <= x0 or y1 <= y0 or z1 <= z0:
        raise ValueError(
            f"invalid box: maxs must exceed mins on all axes, got {mins} -> {maxs}"
        )

    overrides = face_textures or {}

    lines: list[str] = ["{\n", f' guid "{mapfile.new_guid()}"\n']
    for side in ("bottom", "top", "south", "north", "west", "east"):
        p1, p2, p3 = _face_points(side, mins, maxs)
        tex = overrides.get(side, texture)  # type: ignore[arg-type]
        lines.append(
            f" ( {_v(p1)} ) ( {_v(p2)} ) ( {_v(p3)} ) {tex} {_FACE_PARAMS}\n"
        )
    lines.append("}\n")
    return "".join(lines)


def _v(point: Point) -> str:
    return f"{point[0]:g} {point[1]:g} {point[2]:g}"


def _face_points(side: Side, mins: Point, maxs: Point) -> tuple[Point, Point, Point]:
    """Three corner points for the named face, in inward-normal winding.

    The set covers all 6 faces of an axis-aligned box. The cross product
    `(B - A) x (C - A)` for each triplet points INTO the brush solid (the
    convention Treyarch's format uses)."""
    x0, y0, z0 = mins
    x1, y1, z1 = maxs

    match side:
        case "bottom":  # outward -Z, inward +Z
            return (x0, y0, z0), (x1, y0, z0), (x1, y1, z0)
        case "top":     # outward +Z, inward -Z
            return (x0, y0, z1), (x1, y1, z1), (x1, y0, z1)
        case "south":   # outward -Y, inward +Y (face at y = y0)
            return (x0, y0, z0), (x1, y0, z1), (x1, y0, z0)
        case "north":   # outward +Y, inward -Y (face at y = y1)
            return (x0, y1, z0), (x1, y1, z0), (x1, y1, z1)
        case "west":    # outward -X, inward +X (face at x = x0)
            return (x0, y0, z0), (x0, y1, z0), (x0, y1, z1)
        case "east":    # outward +X, inward -X (face at x = x1)
            return (x1, y0, z0), (x1, y0, z1), (x1, y1, z0)
    raise ValueError(f"unknown face side: {side!r}")


def normalize_box(mins: Point, maxs: Point) -> tuple[Point, Point]:
    """Reorder so each axis of `mins` is <= the corresponding axis of `maxs`."""
    x0, y0, z0 = mins
    x1, y1, z1 = maxs
    if x0 > x1: x0, x1 = x1, x0
    if y0 > y1: y0, y1 = y1, y0
    if z0 > z1: z0, z1 = z1, z0
    return (x0, y0, z0), (x1, y1, z1)
