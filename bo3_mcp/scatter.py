"""TD-driven prop scatter — environmental "life" on generated terrain.

terrain-diffusion gives us a heightmap (the floor). This module scatters
stock BO3 prop xmodels (foliage / debris / rubble) across that surface to
make it feel alive instead of an empty plane.

Key safety property: every scattered prop is a `misc_model` with
`no_collmap "1"` — NO collision map. This is deliberate. We spent the
whole patch-terrain effort proving that height discontinuities break
zombie navmesh tracking; a collidable prop dropped on a path would
re-introduce exactly that class of bug. `no_collmap` props are purely
visual: the player and zombies walk straight through them. So scatter
can NEVER break pathing, by construction.

The scatter primitive:

    "classname"  "misc_model"
    "model"      "<xmodel>"
    "angles"     "0 <yaw> 0"
    "origin"     "<x> <y> <z>"     # z from the terrain sidecar
    "modelscale" "<scale>"
    "no_collmap" "1"               # visual only — zero nav impact
    "static"     "1"
    "lightingstate1..4" "1"

Verified-stock entity shape (mined from
`_prefabs/zm/zm_giant/zm_giant_geo.map`). xmodels referenced by a
misc_model are auto-collected by the asset pipeline (same mechanism
that pulls perk/barricade prefab models) — no manual zone-manifest
entry needed. STILL must be runtime-verified per category (compile
pass ≠ in-game pass); that's what `zm_scatter_lab` is for.
"""

from __future__ import annotations

import random
from typing import Sequence

from . import entities, mapfile, paths

Point2 = tuple[float, float]

# ── Vetted prop catalog ────────────────────────────────────────────────
# Every model name below was extracted from an actual `"model"` KVP in
# shipping BO3 mod-tools .map sources (zm_giant, mp_sector, _prefabs/**).
# Grouped by category so a recipe can pick a palette per environment.
# `weight` biases the weighted random pick (higher = more common).

PROP_CATALOG: dict[str, list[tuple[str, float]]] = {
    # Low ground cover — densest, smallest. The bread-and-butter of
    # "this isn't an empty plane".
    # v23.23: the tall, WIDE cluster models (p7_foliage_grass_tall_
    # cluster_sml/med) were dropped. Z placement is exact (bilinear,
    # proven 0.00u off the mesh), but a single ground point can't make a
    # big splayed cluster conform to terrain that rises/falls UNDER its
    # own footprint — on broken_floor's steep patch sides the ground
    # varies 20-40u across one cluster, so its downhill blades hang in
    # the air. That was the "floating grass". Compact single tufts have
    # a small footprint and conform far better; remaining float is
    # absorbed by the slope-aware sink (see scatter_props sink_*).
    "grass": [
        ("p7_foliage_grass_02", 3.0),
        ("p7_foliage_grass_dry_02", 3.0),
        ("p7_foliage_grass_dry_03", 2.0),
        ("p7_foliage_grass_flowers", 1.0),
        ("p7_eth_foliage_grass_small_wild", 2.0),
    ],
    # Mid debris — sparser, breaks up the ground texture. ONLY small
    # chunky props here: they sit on a single point and tolerate bumpy
    # TD terrain. Flat-lying props (plywood sheets etc.) are excluded
    # on purpose — a flat board can't conform to undulating terrain and
    # visibly clips through it (v23.15 playtest). Flat props belong on
    # a flat preset / leveled pad, not scattered on broken_floor.
    "debris": [
        ("p7_debris_concrete_rubble_sm_02", 2.0),
        ("p7_debris_concrete_rubble_sm_14", 2.0),
    ],
    # Larger set-dressing props — rare, deliberate. These are bigger so
    # keep their weight/density low or they look littered.
    "props": [
        ("p7_crate_wood_01", 1.0),
        ("p7_sandbag_02", 1.0),
    ],
    # Snow variants (for snowy / zm_giant-style maps).
    "grass_snow": [
        ("p7_foliage_grass_dry_01_snow", 3.0),
        ("p7_foliage_grass_dry_02_snow", 3.0),
        ("p7_foliage_grass_dry_03_snow", 2.0),
    ],
}


def _weighted_pick(rng: random.Random,
                    weighted: list[tuple[str, float]]) -> str:
    total = sum(w for _, w in weighted)
    r = rng.uniform(0.0, total)
    upto = 0.0
    for name, w in weighted:
        upto += w
        if r <= upto:
            return name
    return weighted[-1][0]


def _misc_model_kvps(model: str, scale: float) -> dict[str, str]:
    return {
        "model": model,
        "no_collmap": "1",   # visual only — cannot affect navmesh/pathing
        "static": "1",
        "modelscale": f"{scale:g}",
        "lightingstate1": "1",
        "lightingstate2": "1",
        "lightingstate3": "1",
        "lightingstate4": "1",
    }


def scatter_props(
    map_name: str,
    *,
    footprint_mins: Point2,
    footprint_maxs: Point2,
    categories: Sequence[str] = ("grass", "debris"),
    spacing: float = 56.0,   # was 96 — denser candidate grid (v23.15 "not enough")
    density: float = 0.75,   # was 0.55 — more cells actually get a prop
    scale_range: tuple[float, float] = (0.8, 1.4),
    exclusions: Sequence[tuple[float, float, float]] | None = None,
    mask_min_alpha: int | None = None,
    edge_margin: float = 48.0,
    seed: int | None = None,
    layer: str = "000_Global/Geo/Scatter",
    fallback_z: float = 16.0,
    # ── v23.23 slope-aware SINK ──────────────────────────────────────
    # terrain_height_at_xy is now exact (bilinear; verified 0.00u off
    # the rendered mesh on 207/207 props). But a finite-width model
    # placed at ONE ground point still can't conform to terrain that
    # varies under its own footprint — on a slope the downhill side
    # hangs in the air. Fix: push the origin BELOW the surface so the
    # model is anchored INTO the ground. Mild ground-clip reads as
    # "planted"; floating never does. Sink grows with local steepness
    # (cell_spread from terrain_height_at_xy). Defaults 0.0 → ZERO
    # behaviour change for callers that don't opt in (e.g. debris pass,
    # hand-authored maps).
    sink_base: float = 0.0,
    sink_slope_factor: float = 0.0,
    sink_max: float = 24.0,
    # On steep cells also cap the random model scale — a big tuft on a
    # near-cliff overhangs the most. None = no cap.
    steep_scale_cap: float | None = None,
    steep_spread_threshold: float = 16.0,
) -> dict:
    """Scatter `misc_model` props across a map's terrain surface.

    Uses a jittered grid (cheap, deterministic, visually even without
    obvious rows). For each candidate cell a prop is placed with
    probability `density`; its Z comes from the terrain sidecar via
    `terrain.terrain_height_at_xy`, so props sit ON the generated
    surface (works for voxel or patch terrain alike).

    Args:
        footprint_mins/maxs: (x, y) bounds to scatter within (typically
            the terrain footprint / room interior).
        categories: which `PROP_CATALOG` palettes to draw from.
        spacing: jittered-grid cell size in BO3 units. Smaller = denser
            placement. 96 ≈ a clump every ~2.4 m.
        density: 0..1 probability a given grid cell gets a prop.
        scale_range: uniform random modelscale per prop.
        exclusions: list of (x, y, radius) keep-clear circles — drop
            any candidate within `radius` of (x, y). Pass the doorway
            pads, spawner XYs, perk XYs, player spawn, etc. so props
            never clip a machine or block a sightline through a door.
        edge_margin: shrink the footprint by this much on every side so
            props don't poke through walls.
        seed: RNG seed for reproducible scatter (None = random).
        layer: Radiant layer for the entities (so they're easy to bulk
            toggle/delete in the editor).
        fallback_z: Z for candidates outside the terrain footprint
            (shouldn't happen if footprint is within the sidecar, but
            safe).

    Returns:
        {"placed": int, "candidates": int, "rejected_exclusion": int,
         "categories": [...], "models_used": {model: count}}
    """
    from . import terrain as _terrain  # late import: terrain is below us

    # Resolve terrain Z once-per-call. If the map has no terrain sidecar
    # (flat-floor / non-TD map) terrain_height_at_xy raises — degrade to
    # fallback_z so scatter still works on plain rooms.
    sidecar_exists = True
    try:
        _terrain.terrain_height_at_xy(map_name, 0.0, 0.0)
    except FileNotFoundError:
        sidecar_exists = False
    except Exception:
        sidecar_exists = True  # other errors: assume sidecar present, surface later

    def _surface(px: float, py: float) -> tuple[float, float]:
        """(surface_z, cell_spread). cell_spread = local relief over one
        terrain cell — drives the slope-aware sink. No sidecar (flat /
        non-TD map) → flat fallback, zero spread."""
        if not sidecar_exists:
            return fallback_z, 0.0
        try:
            h = _terrain.terrain_height_at_xy(map_name, px, py)
            if h.get("found"):
                return h["z"], float(h.get("cell_spread", 0.0))
            return fallback_z, 0.0
        except FileNotFoundError:
            return fallback_z, 0.0

    bad = [c for c in categories if c not in PROP_CATALOG]
    if bad:
        raise ValueError(
            f"unknown scatter categories {bad}; valid: {sorted(PROP_CATALOG)}"
        )
    palette: list[tuple[str, float]] = []
    for c in categories:
        palette.extend(PROP_CATALOG[c])
    if not palette:
        raise ValueError("scatter palette is empty")

    # v23.18: optional grassiness-mask gate. When mask_min_alpha is set,
    # read the blend_mask the terrain pass persisted to the sidecar and
    # only place a prop where the mask alpha at that XY is >= the
    # threshold. Used for the grass pass so grass clumps grow on the
    # grass-FLOOR (high mask) and bare dirt stays bald (low mask) —
    # one shared field drives both layers.
    mask_grid = None
    mask_ox = mask_oy = 0.0
    mask_cs = 1.0
    if mask_min_alpha is not None:
        try:
            import json as _json
            sc = _terrain._terrain_sidecar_path(map_name)
            with open(sc, "r", encoding="utf-8") as f:
                bm = _json.load(f).get("blend_mask")
            if bm:
                mask_grid = bm["alpha_grid"]            # [y][x] 0-255
                mask_ox, mask_oy, _mz = bm["origin"]
                mask_cs = bm["cell_size"]
        except (OSError, KeyError, ValueError):
            mask_grid = None  # no mask → gate is a no-op

    def _mask_ok(px: float, py: float) -> bool:
        if mask_grid is None:
            return True
        xi = int(round((px - mask_ox) / mask_cs))
        yi = int(round((py - mask_oy) / mask_cs))
        if 0 <= yi < len(mask_grid) and 0 <= xi < len(mask_grid[0]):
            return mask_grid[yi][xi] >= mask_min_alpha
        return True  # outside the mask grid → don't gate

    rng = random.Random(seed)
    min_x, min_y = footprint_mins
    max_x, max_y = footprint_maxs
    min_x += edge_margin
    min_y += edge_margin
    max_x -= edge_margin
    max_y -= edge_margin
    if max_x <= min_x or max_y <= min_y:
        raise ValueError(
            f"footprint too small after edge_margin={edge_margin}: "
            f"({min_x},{min_y})..({max_x},{max_y})"
        )

    excl = list(exclusions or [])

    def _excluded(px: float, py: float) -> bool:
        for ex, ey, er in excl:
            if (px - ex) ** 2 + (py - ey) ** 2 <= er * er:
                return True
        return False

    target = paths.map_source(map_name)
    mf = mapfile.load(target)

    placed = 0
    candidates = 0
    rejected = 0
    models_used: dict[str, int] = {}
    placed_positions: list[tuple[float, float]] = []

    y = min_y
    while y < max_y:
        x = min_x
        while x < max_x:
            candidates += 1
            if rng.random() <= density:
                # Jitter within the cell so the grid isn't visible.
                jx = x + rng.uniform(0.0, spacing)
                jy = y + rng.uniform(0.0, spacing)
                if (jx <= max_x and jy <= max_y
                        and not _excluded(jx, jy)
                        and _mask_ok(jx, jy)):
                    sz, spread = _surface(jx, jy)
                    sink = sink_base + sink_slope_factor * spread
                    if sink > sink_max:
                        sink = sink_max
                    pz = sz - sink
                    model = _weighted_pick(rng, palette)
                    s_lo, s_hi = scale_range
                    if (steep_scale_cap is not None
                            and spread > steep_spread_threshold):
                        s_hi = min(s_hi, steep_scale_cap)
                        if s_lo > s_hi:
                            s_lo = s_hi
                    scale = rng.uniform(s_lo, s_hi)
                    yaw = rng.uniform(0.0, 360.0)
                    entities.add_entity(
                        mf, "misc_model",
                        origin=(float(jx), float(jy), float(pz)),
                        angles=(0.0, float(yaw), 0.0),
                        layer=layer,
                        kvps=_misc_model_kvps(model, scale),
                    )
                    placed += 1
                    placed_positions.append((float(jx), float(jy)))
                    models_used[model] = models_used.get(model, 0) + 1
                elif _excluded(jx, jy):
                    rejected += 1
            x += spacing
        y += spacing

    mf.save(target)
    return {
        "placed": placed,
        "candidates": candidates,
        "rejected_exclusion": rejected,
        "categories": list(categories),
        "models_used": models_used,
        "footprint": [[min_x, min_y], [max_x, max_y]],
        # v23.23 sink diagnostics — echo what was applied so the lab
        # output shows it without re-reading the .map.
        "sink": {
            "base": sink_base,
            "slope_factor": sink_slope_factor,
            "max": sink_max,
            "steep_scale_cap": steep_scale_cap,
            "steep_spread_threshold": steep_spread_threshold,
        },
        # v23.19: exact XY of every placed prop. The terrain recipe
        # uses the grass pass's positions to paint a grass-floor blend
        # halo EXACTLY under the scatter (not an independent mask).
        "positions": placed_positions,
    }
