"""Z-probe overlay for the scatter-float lab.

Run AFTER `make_terrain_zombie_arena('zm_scatter_lab')` has generated the
source. Drops a regular grid of hard-edged crate markers, each placed at
EXACTLY `terrain.terrain_height_at_xy(x, y)["z"]`, across the whole
terrain footprint — flat cells AND broken_floor's steep raised patches.

The point: a crate has a flat bottom and sharp edges. If the Z lookup
matches the rendered patch-mesh surface, every crate sits flush on the
ground (slopes included). Any float/sink is glaringly obvious against a
crate in a way it never is against organic grass. This is the visual
proof that bilinear `terrain_height_at_xy` conforms to the surface.

Standalone (not an MCP tool) so it imports the CURRENT bo3_mcp.terrain
directly — sidesteps the MCP server's module cache entirely (the exact
trap that made v23.21's bilinear look like it failed).

    python tests/labs/scatter_zprobe_lab.py

Idempotent-ish: re-running appends another probe layer; rebuild from a
fresh `make_terrain_zombie_arena` if you want a clean slate.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bo3_mcp import entities, mapfile, paths, terrain  # noqa: E402

MAP = "zm_scatter_lab"
PROBE_MODEL = "p7_crate_wood_01"   # flat-bottomed, sharp edges, in catalog
PROBE_LAYER = "ZPROBE"
GRID_STEP = 80.0                   # world units between probe markers


def main() -> None:
    side = terrain._terrain_sidecar_path(MAP)
    with open(side, "r", encoding="utf-8") as f:
        d = json.load(f)
    ox, oy, _oz = d["origin"]
    cs = float(d["cell_size"])
    hm = d["scaled_heightmap"]
    rows, cols = len(hm), len(hm[0])
    x_lo, x_hi = ox, ox + cols * cs
    y_lo, y_hi = oy, oy + rows * cs

    target = paths.map_source(MAP)
    mf = mapfile.load(target)

    placed = 0
    on_steep = 0          # probes whose cell_spread > 16u (a real slope)
    max_spread = 0.0
    spreads: list[float] = []

    y = y_lo + GRID_STEP
    while y < y_hi - GRID_STEP:
        x = x_lo + GRID_STEP
        while x < x_hi - GRID_STEP:
            h = terrain.terrain_height_at_xy(MAP, x, y)
            if h.get("found"):
                spread = float(h.get("cell_spread", 0.0))
                spreads.append(spread)
                max_spread = max(max_spread, spread)
                if spread > 16.0:
                    on_steep += 1
                entities.add_entity(
                    mf, "misc_model",
                    origin=(float(x), float(y), float(h["z"])),
                    angles=(0.0, 0.0, 0.0),
                    layer=PROBE_LAYER,
                    kvps={
                        "model": PROBE_MODEL,
                        "no_collmap": "1",   # visual only, zero nav impact
                        "static": "1",
                        "modelscale": "1",
                        "lightingstate1": "1",
                        "lightingstate2": "1",
                        "lightingstate3": "1",
                        "lightingstate4": "1",
                    },
                )
                placed += 1
            x += GRID_STEP
        y += GRID_STEP

    mf.save(target)

    steep_pct = (100.0 * on_steep / placed) if placed else 0.0
    print(f"Z-probe overlay written to {target}")
    print(f"  probes placed         : {placed}")
    print(f"  on steep cells (>16u) : {on_steep} ({steep_pct:.0f}%)  "
          f"<- these markers land on broken_floor's raised-patch slopes")
    print(f"  max cell_spread seen  : {max_spread:.1f}u over one {cs:.0f}u cell")
    if spreads:
        big = sum(1 for s in spreads if s > 24.0)
        print(f"  probes on >24u cliffs : {big}  (the worst-case float locations)")
    print("\nExpected in-game: every crate flush on the ground, INCLUDING "
          "the ones on the steep dirt patches. Any floating/sunk crate = "
          "Z lookup still wrong.")


if __name__ == "__main__":
    main()
