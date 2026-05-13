"""Heightmap-driven terrain generation for BO3 maps.

This module solves the *import* half of "generate outdoor terrain":
turning a 2D heightmap (NxM array of elevations) into BO3 box-brush
geometry that the BSP compiler can ingest.

The generation half (where the heightmap comes from) is intentionally
pluggable: this module ships a no-dependency `value_noise_heightmap`
that produces organic-looking fractal terrain, but you can pass any
2D float array — from Perlin, from Treyarch's brush stamps converted
to elevation, or from an ML model like xandergos/terrain-diffusion
(future integration; the heightmap interface is the contract).

Design choices:

- **Voxel grid, not patch mesh.** Each cell of the heightmap becomes
  one axis-aligned box brush. Looks chunky/Minecraft-y but works in
  vanilla MCP and renders with full lighting/collision. Patch meshes
  (Treyarch's smooth curved-surface terrain) would need a separate
  parser/writer in `mapfile.py` — postponed to v2+.

- **Quad merging is optional, off by default.** A naive 64×64 grid
  emits 4096 brushes which is fine for a single playable area but
  hits BSP limits if you spam several. `merge_strips=True` collapses
  runs of same-height cells along the X axis (typical 30-60%
  reduction on smooth heightmaps).

- **Per-height texture bands.** Pass `height_bands=[(z_max, texture),
  ...]` to get dirt-at-base, rock-mid, snow-top. Default is single
  texture (concrete pebbles — neutral wasteland).

BO3 coordinate convention: Z is up, units are inches. Floor of typical
zone interiors lives at world Z=16 (16-thick floor slabs at Z=[0..16]).
For exterior terrain, prefer base_z=0 so terrain sits at the same
"ground level" as your scaffolded rooms.
"""

from __future__ import annotations

import math
import os
import random
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Sequence

from . import brushes, geometry, mapfile, paths

# --- Heightmap import (the hard half) ---------------------------------------


def heightmap_to_brushes(
    map_name: str,
    heightmap: Sequence[Sequence[float]],
    *,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    cell_size: float = 64.0,
    height_scale: float = 1.0,
    texture: str = "t7_concrete_pebbles_cracked",
    height_bands: list[tuple[float, str]] | None = None,
    skip_below: float = 0.0,
    max_brushes: int = 8192,
    merge_strips: bool = False,
) -> dict:
    """Convert a 2D heightmap into a voxel grid of box brushes.

    Each cell `heightmap[y][x]` becomes a box extending from `origin.z`
    upward by `heightmap[y][x] * height_scale` units. The cell's XY
    footprint is `cell_size × cell_size`, positioned at
    `(origin.x + x*cell_size, origin.y + y*cell_size)`.

    Args:
        heightmap: 2D sequence of floats (rows of columns). Values are
            multiplied by `height_scale` to produce world-unit heights.
        origin: world position of the (col=0, row=0) corner. Terrain
            extends in +X (columns) and +Y (rows) from here. Z is the
            base — brushes start at this Z and grow upward.
        cell_size: XY extent of each cell (default 64 — a 10×10 grid is
            640×640 units, roughly a single room's footprint).
        height_scale: multiplier on heightmap values. With normalized
            [0,1] heightmaps, use height_scale=64..256 for hill-sized
            terrain, 256..512 for mountain-sized.
        texture: fallback texture if no `height_bands` match.
        height_bands: list of `(top_z, texture)` pairs for per-elevation
            texturing. Bands are evaluated in order; the first whose
            `top_z` exceeds the cell's top Z wins. Example:
                [(20, "dirt"), (80, "rock"), (1e9, "snow_pack")]
        skip_below: don't emit a brush for cells with height ≤ this
            (in heightmap units, before scaling). Use to leave "valleys"
            as void / unwalkable holes. 0 means emit everything > 0.
        max_brushes: safety budget. Raises ValueError if the grid would
            exceed this. Default 8192 handles a 90×90 grid; raise it
            cautiously — BSP compile cost scales with brush count.
        merge_strips: if True, runs of same-height cells along the X
            axis are merged into single brushes. Typical 30-60%
            reduction on smooth terrain. Off by default for predictability.

    Returns: dict with brush count, grid footprint, and texture usage.
    """
    rows = len(heightmap)
    cols = len(heightmap[0]) if rows else 0
    if rows == 0 or cols == 0:
        raise ValueError("heightmap is empty")
    # Validate it's rectangular
    for i, row in enumerate(heightmap):
        if len(row) != cols:
            raise ValueError(
                f"heightmap row {i} has length {len(row)}; expected {cols}"
            )

    estimated = sum(1 for row in heightmap for v in row if v > skip_below)
    if estimated > max_brushes:
        raise ValueError(
            f"heightmap would emit ~{estimated} brushes (> max_brushes={max_brushes}). "
            f"Downsample the grid, raise the budget, or enable merge_strips."
        )

    ox, oy, oz = origin
    cs = float(cell_size)
    hs = float(height_scale)

    def _pick_texture(top_z: float) -> str:
        if not height_bands:
            return texture
        for band_top, band_tex in height_bands:
            if top_z <= band_top:
                return band_tex
        return texture

    brush_specs: list[tuple[tuple[float, float, float],
                            tuple[float, float, float],
                            str]] = []

    if merge_strips:
        # Greedy horizontal run merging: walk each row, group consecutive
        # cells with the SAME quantized height into one brush. Quantize
        # to nearest unit to handle float noise.
        for y in range(rows):
            x = 0
            while x < cols:
                h = heightmap[y][x]
                if h <= skip_below:
                    x += 1
                    continue
                quant = round(h * hs)  # quantized world-Z extent
                x_start = x
                # Extend run while quantized heights match
                while (x + 1 < cols
                       and heightmap[y][x + 1] > skip_below
                       and round(heightmap[y][x + 1] * hs) == quant):
                    x += 1
                cell_mins = (ox + x_start * cs, oy + y * cs, oz)
                cell_maxs = (ox + (x + 1) * cs, oy + (y + 1) * cs, oz + quant)
                brush_specs.append((cell_mins, cell_maxs, _pick_texture(oz + quant)))
                x += 1
    else:
        for y in range(rows):
            for x in range(cols):
                h = heightmap[y][x]
                if h <= skip_below:
                    continue
                top_z = oz + h * hs
                cell_mins = (ox + x * cs, oy + y * cs, oz)
                cell_maxs = (ox + (x + 1) * cs, oy + (y + 1) * cs, top_z)
                brush_specs.append((cell_mins, cell_maxs, _pick_texture(top_z)))

    mf, ws = geometry._load_top(map_name)
    texture_counts: dict[str, int] = {}
    for bm, bM, tex in brush_specs:
        ws.brushes.append(brushes.box_brush(bm, bM, tex))
        texture_counts[tex] = texture_counts.get(tex, 0) + 1
    geometry._save_top(mf, map_name)

    return {
        "brushes_added": len(brush_specs),
        "grid_shape": (rows, cols),
        "cell_size": cs,
        "height_scale": hs,
        "origin": origin,
        "footprint_mins": (ox, oy, oz),
        "footprint_maxs": (ox + cols * cs, oy + rows * cs, oz),
        "texture_usage": texture_counts,
        "merge_strips": merge_strips,
        "worldspawn_total_brushes": len(ws.brushes),
    }


# --- Heightmap generation (the easy half — pluggable backends) -------------


def value_noise_heightmap(
    width: int,
    height: int,
    *,
    scale: float = 0.1,
    octaves: int = 4,
    persistence: float = 0.5,
    lacunarity: float = 2.0,
    seed: int | None = None,
) -> list[list[float]]:
    """Generate a heightmap via fractal value noise — no external deps.

    "Value noise" interpolates between random values on an integer
    lattice. It's not as smooth as Perlin/Simplex but is simple and
    produces convincingly organic terrain. Fractal sums of value noise
    at different frequencies ("octaves") give natural-looking hills.

    Output values are in roughly [0, 1] but can spike slightly outside
    that range due to interpolation; pair with `height_scale` in
    `heightmap_to_brushes` to convert to world units.

    Args:
        width, height: grid dimensions (columns × rows).
        scale: lattice spacing. Smaller = larger features (broader hills).
            0.05 = sweeping mountains, 0.2 = jagged hills.
        octaves: number of noise layers stacked. Higher = more detail.
            Each octave doubles frequency and halves amplitude (default
            persistence=0.5, lacunarity=2.0).
        persistence: amplitude falloff per octave (0..1). Lower = smoother.
        lacunarity: frequency increase per octave. Higher = more roughness.
        seed: RNG seed for reproducible output. None = randomized.

    Returns: list of `height` lists of `width` floats.
    """
    rng = random.Random(seed)

    def _smooth(t: float) -> float:
        # Hermite smoothstep — softens the linear interp between lattice points.
        return t * t * (3.0 - 2.0 * t)

    def _lattice_table(freq_scale: float) -> dict[tuple[int, int], float]:
        # Build a dictionary mapping integer lattice coords to random values.
        # Lazy generation would be more memory-efficient for large grids;
        # for our typical 32×32 to 128×128 sizes a dict is fine.
        table: dict[tuple[int, int], float] = {}
        x_max = int(math.ceil(width * scale * freq_scale)) + 2
        y_max = int(math.ceil(height * scale * freq_scale)) + 2
        for ly in range(-1, y_max):
            for lx in range(-1, x_max):
                table[(lx, ly)] = rng.random()
        return table

    def _sample(table: dict[tuple[int, int], float],
                xf: float, yf: float) -> float:
        x0 = int(math.floor(xf))
        y0 = int(math.floor(yf))
        x1, y1 = x0 + 1, y0 + 1
        u = _smooth(xf - x0)
        v = _smooth(yf - y0)
        v00 = table.get((x0, y0), 0.0)
        v10 = table.get((x1, y0), 0.0)
        v01 = table.get((x0, y1), 0.0)
        v11 = table.get((x1, y1), 0.0)
        a = v00 + u * (v10 - v00)
        b = v01 + u * (v11 - v01)
        return a + v * (b - a)

    # Precompute lattice tables per octave (each octave uses a different seed
    # path via the same rng, so they're decorrelated).
    tables: list[tuple[dict[tuple[int, int], float], float, float]] = []
    freq = 1.0
    amp = 1.0
    total_amp = 0.0
    for _ in range(octaves):
        tables.append((_lattice_table(freq), freq, amp))
        total_amp += amp
        freq *= lacunarity
        amp *= persistence

    out: list[list[float]] = [[0.0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            v = 0.0
            for table, freq, amp in tables:
                v += amp * _sample(table, x * scale * freq, y * scale * freq)
            out[y][x] = v / total_amp  # normalize to ~[0, 1]
    return out


# --- Convenience: one-call generate-and-place ------------------------------


def generate_terrain(
    map_name: str,
    *,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    grid_size: tuple[int, int] = (32, 32),
    cell_size: float = 64.0,
    max_height: float = 128.0,
    seed: int | None = None,
    scale: float = 0.08,
    octaves: int = 4,
    height_bands: list[tuple[float, str]] | None = None,
    merge_strips: bool = True,
    skip_below_normalized: float = 0.0,
) -> dict:
    """One-shot: generate a value-noise heightmap and emit it as brushes.

    Args:
        map_name: target map.
        origin: world position of the terrain's (0,0) corner.
        grid_size: (width, height) in cells.
        cell_size: XY extent of each cell.
        max_height: world-units that a heightmap value of 1.0 maps to.
        seed: noise seed for reproducibility.
        scale, octaves: passed to `value_noise_heightmap`.
        height_bands: per-elevation textures. If None, uses a default
            wasteland palette (dirt → rock → cracked-stone).
        merge_strips: collapse same-height runs into single brushes (default
            True for terrain since adjacent cells often quantize identically).
        skip_below_normalized: skip cells whose heightmap value (BEFORE
            scaling) is ≤ this. Useful for "carve valleys/water" effects
            — pass 0.2 to skip the lowest 20% of terrain.

    Sensible defaults give you a 32×32 grid (2048 footprint per side),
    height 0..128, mid-roughness terrain. Tune from there.
    """
    if height_bands is None:
        oz = origin[2]
        height_bands = [
            (oz + max_height * 0.3,  "t7_concrete_pebbles_cracked"),  # low: dirt-like
            (oz + max_height * 0.7,  "t7_concrete_wall_dark_01"),     # mid: rock
            (oz + max_height * 1.5,  "t7_concrete_bare_dark_01_wet"), # high: crag
        ]

    cols, rows = grid_size
    heightmap = value_noise_heightmap(
        cols, rows,
        scale=scale, octaves=octaves, seed=seed,
    )

    return heightmap_to_brushes(
        map_name, heightmap,
        origin=origin,
        cell_size=cell_size,
        height_scale=max_height,
        height_bands=height_bands,
        skip_below=skip_below_normalized,
        merge_strips=merge_strips,
        # Generous budget — value-noise terrain at 64x64 = 4096 cells uncompressed,
        # ~50% of those after merge.
        max_brushes=16384,
    )


# --- ML backend: xandergos/terrain-diffusion via REST API ------------------


DEFAULT_TD_SERVER = "http://localhost:8000"


def fetch_terrain_diffusion_region(
    i1: int, j1: int, i2: int, j2: int,
    *,
    scale: int = 1,
    seed: int | None = None,
    allow_constant: bool = False,
    server_url: str = DEFAULT_TD_SERVER,
    timeout: float = 180.0,
) -> tuple[list[list[float]], dict]:
    """Hit the xandergos/terrain-diffusion REST API and return a heightmap
    region in METERS, plus parsed climate channels and per-channel stats.

    The server must be running — see `start_terrain_diffusion_server` or
    the CLAUDE.md "Terrain-diffusion runtime" section for install.

    Args:
        i1, j1, i2, j2: bounding box in target-resolution pixel coords.
            (i,j) = (x,y) — `i1,j1` is top-left, `i2,j2` is bottom-right.
            Width = i2-i1, height = j2-j1. Each pixel covers `90/scale`
            meters on the 90m model, `30/scale` on the 30m model.
        scale: integer multiplier vs base resolution. 1=base, 2=2x, 4=4x.
        seed: optional world seed. Upstream `/terrain` accepts a `seed`
            query param that triggers `world.change_seed(seed)` server-
            side, clearing the cache and rebuilding. None = use whatever
            seed the running server has.
        allow_constant: if True, don't raise on all-zero elevation
            (otherwise such responses are treated as silent NaN-cast
            failures and rejected). Set True only when you intentionally
            want flat ocean output.
        server_url: base URL of the terrain-diffusion REST API server.
        timeout: HTTP timeout in seconds. Diffusion sampling on CPU can
            take minutes for large regions.

    Returns:
        (heightmap, meta) where:
        - heightmap: 2D list of floats, dimensions (height, width). Values
          are elevation in METERS (floored int16-cast back to float).
          Negative values mean ocean/below-sea-level. Feed to
          `heightmap_to_brushes` directly or via `generate_terrain_diffusion`.
        - meta: dict with width/height, elev min/max/range/distinct, raw
          climate bytes, per-channel climate stats, and payload sizes.

    Raises:
        ConnectionError: server unreachable.
        ValueError: bad payload size, non-finite climate, or all-zero
            elevation (when allow_constant=False)."""
    width = i2 - i1
    height = j2 - j1
    if width <= 0 or height <= 0:
        raise ValueError(f"empty region: i1={i1}, i2={i2}, j1={j1}, j2={j2}")
    if width * height > 2048 * 2048:
        raise ValueError(
            f"region {width}x{height} is too large; the server will OOM. "
            "Use scale<8 or split into tiles."
        )

    url = (f"{server_url.rstrip('/')}/terrain"
           f"?i1={i1}&j1={j1}&i2={i2}&j2={j2}&scale={scale}")
    if seed is not None:
        url += f"&seed={seed}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"Couldn't reach terrain-diffusion API at {server_url}: {e}. "
            "Start the server first via start_terrain_diffusion_server "
            "(or check CLAUDE.md \"Terrain-diffusion runtime\")."
        ) from e

    # Response layout: elevation int16-LE (H*W*2 bytes) + climate float32-LE
    # interleaved (H, W, 4 channels = H*W*16 bytes). The climate block is
    # always present in the current API.
    elev_bytes = height * width * 2
    climate_bytes_expected = height * width * 4 * 4
    expected_total = elev_bytes + climate_bytes_expected
    if len(data) == elev_bytes:
        # No climate (theoretical fallback). Treat as missing.
        climate_bytes = b""
    elif len(data) == expected_total:
        climate_bytes = data[elev_bytes:]
    else:
        raise ValueError(
            f"unexpected payload length {len(data)}; expected "
            f"{elev_bytes} (elev only) or {expected_total} (elev + climate). "
            "Did the request fail silently or the API change?"
        )

    heightmap: list[list[float]] = []
    for y in range(height):
        row_offset = y * width * 2
        row = list(struct.unpack_from(f"<{width}h", data, row_offset))
        heightmap.append([float(v) for v in row])

    min_e = min(min(r) for r in heightmap)
    max_e = max(max(r) for r in heightmap)
    distinct = len({v for row in heightmap for v in row})

    # Climate finite check (full scan, not sampled). Compute per-channel
    # stats for the preview tool. Climate layout: (H, W, 4) interleaved
    # so the c-th channel lives at indices [c::4] of the flat float array.
    climate_channel_stats: list[dict] = []
    if climate_bytes:
        n_floats = width * height * 4
        if len(climate_bytes) != n_floats * 4:
            raise ValueError(
                f"climate block size {len(climate_bytes)} != expected "
                f"{n_floats * 4} ({width}x{height}x4xf32)"
            )
        flat = struct.unpack(f"<{n_floats}f", climate_bytes)
        # Detect NaN OR Inf
        nonfinite = sum(1 for v in flat if v != v or v == math.inf or v == -math.inf)
        if nonfinite:
            raise ValueError(
                f"terrain-diffusion climate has {nonfinite}/{n_floats} "
                "non-finite values (NaN/Inf). Most common cause: mismatched "
                "decoder_tile_size / decoder_tile_stride pair (stride must "
                "be <= tile_size). Restart server with paired kwargs."
            )
        for c, name in enumerate(("temp_C", "t_season", "precip_mm", "p_cv")):
            ch = flat[c::4]
            climate_channel_stats.append({
                "channel": c,
                "name": name,
                "min": min(ch),
                "max": max(ch),
                "mean": sum(ch) / len(ch),
            })

    if not allow_constant and distinct == 1 and min_e == 0 and max_e == 0:
        # All-zero elevation: previously the silent NaN signature. Now
        # that we full-scan climate above, this is more likely real flat
        # ocean — but emitting 0 brushes is still useless, so refuse by
        # default. Caller passes allow_constant=True to override.
        raise ValueError(
            "terrain-diffusion returned all-zero elevation (1024 cells, "
            "min=max=0). With finite climate this is either rare-but-real "
            "flat sea floor or model misbehavior. Refusing to emit zero "
            "brushes. Pass allow_constant=True to override, or pick a "
            "different region/seed."
        )

    return heightmap, {
        "width": width,
        "height": height,
        "elev_byte_count": elev_bytes,
        "climate_byte_count": len(climate_bytes),
        "n_climate_channels": 4,  # temp, t_season, precip, p_cv
        "climate_raw": climate_bytes,
        "climate_channel_stats": climate_channel_stats,
        "min_elev_m": min_e,
        "max_elev_m": max_e,
        "elev_range_m": max_e - min_e,
        "distinct_elev_values": distinct,
        "seed_used": seed,
    }


def preview_terrain_diffusion_region(
    *,
    region: tuple[int, int, int, int] = (0, 0, 32, 32),
    scale: int = 1,
    seed: int | None = None,
    sea_level_m: float = 0.0,
    world_units_per_meter: float = 0.3,
    cell_size: float = 64.0,
    server_url: str = DEFAULT_TD_SERVER,
) -> dict:
    """Non-mutating probe of a terrain-diffusion region.

    Fetches the same data `generate_terrain_diffusion` would, but
    instead of emitting brushes returns a summary you can use to pick
    `seed`, `sea_level_m`, `world_units_per_meter`, and `cell_size`
    before committing. Cheap (one HTTP request, no map I/O).

    Use this iteratively when scouting for terrain: probe a few seeds,
    look at `elev_range_m` and `recommended_*` fields, then call
    `generate_terrain_diffusion` with the chosen seed/normalize flag.

    Args:
        region, scale, seed, server_url: same as
            `fetch_terrain_diffusion_region`.
        sea_level_m, world_units_per_meter, cell_size: candidate
            scaling params; the preview computes how many brushes
            you'd get with them, plus recommendations.

    Returns: dict with elevation stats, climate stats, brush-count
        estimates under different scaling strategies, and
        recommendations for sea_level_m / normalize_elevation."""
    i1, j1, i2, j2 = region
    heightmap, meta = fetch_terrain_diffusion_region(
        i1, j1, i2, j2,
        scale=scale, seed=seed, server_url=server_url,
        # Don't refuse all-zero in preview mode — caller wants to see it.
        allow_constant=True,
    )

    all_vals = [v for row in heightmap for v in row]
    width = meta["width"]
    height = meta["height"]
    total_cells = width * height
    local_min = meta["min_elev_m"]
    local_max = meta["max_elev_m"]
    elev_range = meta["elev_range_m"]

    # Brush-count estimate under current sea_level_m (clamps below to 0,
    # which `heightmap_to_brushes` then skips via `skip_below=0.0`).
    above_sea = sum(1 for v in all_vals if (v - sea_level_m) * world_units_per_meter > 0)
    # Brush-count estimate with normalize_elevation=True (offset by local_min).
    # After offset, only the literal minimum cell is at 0; everything else
    # is above. Floor for "emit" is > 0, so the minimum cell skips.
    normalized = sum(1 for v in all_vals if (v - local_min) * world_units_per_meter > 0)

    # Recommendation: if 0 brushes would land under current settings,
    # recommend normalization or a sea_level just below the minimum.
    if above_sea == 0:
        recommended_sea_level_m = float(local_min) - 1.0
        recommendation_note = (
            f"Under current sea_level_m={sea_level_m}, the whole region "
            f"is submerged ({local_min:.0f}..{local_max:.0f} m) and would "
            f"emit 0 brushes. Either pass normalize_elevation=True (treats "
            f"bathymetry as relief), or set "
            f"sea_level_m={recommended_sea_level_m:.0f} to put 'ground' "
            f"just below the lowest cell."
        )
    elif above_sea < total_cells * 0.05:
        recommended_sea_level_m = sea_level_m
        recommendation_note = (
            f"Only {above_sea}/{total_cells} cells ({100*above_sea/total_cells:.0f}%) "
            "are above current sea_level_m — most of the region would be "
            "empty. Consider normalize_elevation=True for full coverage."
        )
    else:
        recommended_sea_level_m = sea_level_m
        recommendation_note = "ok — current settings produce usable coverage"

    # Recommended world_units_per_meter targets a max BO3 z-height of ~256
    # (one room's worth) for the highest cell. Useful when you don't yet
    # know how tall the local relief is.
    if elev_range > 0:
        rec_wupm_room = 256 / elev_range  # one room (=256 units) of relief
        rec_wupm_arena = 512 / elev_range  # 2-room mountain
    else:
        rec_wupm_room = world_units_per_meter
        rec_wupm_arena = world_units_per_meter

    return {
        "region": region,
        "scale": scale,
        "seed": seed,
        "width": width,
        "height": height,
        "total_cells": total_cells,
        "elev": {
            "min_m": local_min,
            "max_m": local_max,
            "range_m": elev_range,
            "distinct_values": meta["distinct_elev_values"],
        },
        "climate_channels": meta["climate_channel_stats"],
        "scaling_estimates": {
            "current_sea_level_m": sea_level_m,
            "current_world_units_per_meter": world_units_per_meter,
            "current_cell_size": cell_size,
            "current_xy_footprint": (width * cell_size, height * cell_size),
            "estimated_brushes_under_sea_level": above_sea,
            "estimated_brushes_with_normalize": normalized,
        },
        "recommendations": {
            "sea_level_m": recommended_sea_level_m,
            "note": recommendation_note,
            "world_units_per_meter_for_room_tall_relief": rec_wupm_room,
            "world_units_per_meter_for_arena_tall_relief": rec_wupm_arena,
            "normalize_elevation_suggested": above_sea < total_cells * 0.05,
        },
    }


def generate_terrain_diffusion(
    map_name: str,
    *,
    region: tuple[int, int, int, int] = (0, 0, 128, 128),
    scale: int = 1,
    seed: int | None = None,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    cell_size: float = 64.0,
    sea_level_m: float = 0.0,
    normalize_elevation: bool = False,
    floor_thickness_units: float = 0.0,
    world_units_per_meter: float = 0.3,
    server_url: str = DEFAULT_TD_SERVER,
    height_bands: list[tuple[float, str]] | None = None,
    merge_strips: bool = True,
    max_brushes: int = 32768,
    allow_constant: bool = False,
) -> dict:
    """Generate BO3 terrain using xandergos/terrain-diffusion as the heightmap
    source. Hits the REST API, converts meters → BO3 inches, places brushes.

    **Strongly recommended**: call `preview_terrain_diffusion_region` first
    to scout the region and pick reasonable values. Real-world elevation
    is often negative (ocean/bathymetry) — without `normalize_elevation`
    those become void and you get zero brushes.

    Args:
        region: (i1, j1, i2, j2) pixel-coordinate bbox in the model's
            output space. (j2-j1) = grid rows, (i2-i1) = grid columns.
            Default 128×128 = 16384 cells, fast to sample but plenty
            detailed.
        scale: resolution multiplier (1 = base, 2 = 2x, 4 = 4x).
        seed: optional world seed (changes terrain layout). Passed
            through to the API; no server restart needed.
        origin: world position of the (i1, j1) corner in BO3 units.
            Z is the base elevation that maps to effective sea level.
        cell_size: XY extent per heightmap pixel in BO3 units.
        sea_level_m: meters from the model's output that map to origin.z
            in world units. Elevations below this become void (no brush).
            Ignored if `normalize_elevation=True`.
        normalize_elevation: if True, use the region's local min elevation
            as the effective sea level. This turns bathymetry (negative
            elevation) into usable relief — the deepest cell becomes the
            "floor" (z=0 + floor_thickness_units) and everything rises
            from there. Recommended for ocean/below-sea-level regions.
        floor_thickness_units: BO3 units of solid ground beneath the
            local minimum (so even the lowest cell emits a brush, not
            void). Default 0 means the lowest cell gets a 0-height brush
            which is then skipped. Set to e.g. 16 to give every cell at
            least a 16-unit-thick floor slab.
        world_units_per_meter: how many BO3 inches one meter of real-world
            elevation maps to. 0.3 = scaled-down realistic mountains
            (a 100m hill becomes 30 BO3 units). For 100-meter ranges of
            relief, 1.0-2.5 reads as proper terrain at room scale.
        server_url: terrain-diffusion API URL (default localhost:8000).
        height_bands: per-elevation textures `[(top_z, "tex"), ...]`.
            Defaults to a wasteland palette.
        merge_strips: collapse same-height X-runs (recommended True).
        max_brushes: safety budget.
        allow_constant: pass through to fetch — only set True if you
            specifically want flat-zero terrain.

    Returns: dict from heightmap_to_brushes plus terrain-diffusion meta
    (min/max elevation, climate channel counts).

    Raises ValueError if the scaled terrain would emit 0 brushes — the
    message tells you whether to enable normalize_elevation or lower
    sea_level_m."""
    i1, j1, i2, j2 = region
    heightmap_m, meta = fetch_terrain_diffusion_region(
        i1, j1, i2, j2,
        scale=scale, seed=seed, server_url=server_url,
        allow_constant=allow_constant,
    )

    # Determine the effective baseline elevation (in meters) that maps to
    # origin.z. With normalize_elevation, we slide the region's local
    # minimum down to be the "ground" level, so even fully-submerged
    # regions become usable relief.
    if normalize_elevation:
        effective_sea_level_m = meta["min_elev_m"]
    else:
        effective_sea_level_m = sea_level_m

    # Convert m → BO3 units, subtract baseline, clamp negatives to 0
    # (BUT add floor_thickness_units for the lowest cell so it still emits).
    scaled: list[list[float]] = []
    for row in heightmap_m:
        scaled_row = []
        for m in row:
            world_z = (m - effective_sea_level_m) * world_units_per_meter + floor_thickness_units
            scaled_row.append(max(0.0, world_z))
        scaled.append(scaled_row)

    # Post-scale guard: error early if we'd emit 0 brushes.
    nonzero_cells = sum(1 for r in scaled for v in r if v > 0)
    if nonzero_cells == 0:
        rec = float(meta["min_elev_m"]) - 1.0
        raise ValueError(
            f"Generated terrain would emit 0 brushes. Region elevation "
            f"range is {meta['min_elev_m']:.0f}..{meta['max_elev_m']:.0f} m, "
            f"all <= effective sea level {effective_sea_level_m:.0f} m. "
            f"Either pass normalize_elevation=True (use bathymetry as "
            f"relief), set sea_level_m={rec:.0f} (just below local min), "
            f"or pass floor_thickness_units=16 to floor every cell. "
            f"Call preview_terrain_diffusion_region first to scout."
        )

    if height_bands is None:
        # Auto-band based on observed elevation range
        oz = origin[2]
        max_z = max((max(r) for r in scaled), default=0.0)
        if max_z > 0:
            height_bands = [
                (oz + max_z * 0.25, "t7_concrete_pebbles_cracked"),  # low: shore/dirt
                (oz + max_z * 0.65, "t7_concrete_wall_dark_01"),     # mid: rock
                (oz + max_z * 1.5,  "t7_concrete_bare_dark_01_wet"), # high: snowcap-ish
            ]

    result = heightmap_to_brushes(
        map_name, scaled,
        origin=origin,
        cell_size=cell_size,
        height_scale=1.0,  # heightmap is already in world units
        height_bands=height_bands,
        skip_below=0.0,
        merge_strips=merge_strips,
        max_brushes=max_brushes,
    )
    result["source"] = "terrain-diffusion"
    result["model_meta"] = {
        "region_pixels": (i2 - i1, j2 - j1),
        "scale": scale,
        "seed": seed,
        "min_elev_m": meta["min_elev_m"],
        "max_elev_m": meta["max_elev_m"],
        "elev_range_m": meta["elev_range_m"],
        "distinct_elev_values": meta["distinct_elev_values"],
        "effective_sea_level_m": effective_sea_level_m,
        "normalize_elevation": normalize_elevation,
        "floor_thickness_units": floor_thickness_units,
        "world_units_per_meter": world_units_per_meter,
    }
    return result


def start_terrain_diffusion_server(
    *,
    model: str = "xandergos/terrain-diffusion-30m",
    port: int = 8000,
    device: str | None = "privateuseone:0",
    repo_dir: str = "D:/projects/terrain-diffusion",
    venv_dir: str | None = None,
    decoder_tile_size: int = 128,
    decoder_tile_stride: int = 96,
    batch_size: int = 1,
    no_compile: bool = True,
    dtype: str = "fp32",
    drop_water_pct: float = 0.0,
    seed: int | None = None,
    extra_kwargs: dict | None = None,
    wait_for_ready: bool = True,
    ready_timeout: float = 300.0,
) -> dict:
    """Spawn the terrain-diffusion REST API server as a background process.

    First-run takes 1-3 minutes (model weights download from Hugging Face
    Hub + loading into VRAM). Subsequent runs are seconds.

    **Runtime isolation**: this server runs in its OWN venv at
    `<repo_dir>/.venv` (Python 3.10 + torch 2.4.1 + torch-directml +
    diffusers etc.), NOT in the MCP's Python. This is because
    `torch-directml` hard-pins `torch==2.4.1`, which has no Python 3.14
    wheels, and the MCP itself targets newer Python. The MCP only talks
    to the server over HTTP — no Python-version coupling.

    **VRAM tuning**: the upstream `decoder_tile_size=512` default OOMs on
    8 GB cards. We default to `decoder_tile_size=128` with a paired
    `decoder_tile_stride=96`. **Critical**: stride must be <= tile_size,
    otherwise the model produces all-NaN output silently (because tiles
    don't overlap and the seam-blending math diverges). If you change
    one, change the other.

    Args:
        model: HF model identifier. Choices the repo ships:
            - xandergos/terrain-diffusion-30m (recommended for games)
            - xandergos/terrain-diffusion-90m (realistic worldbuilding)
        port: where the Flask server binds.
        device: torch device string passed via --device AND
            TERRAIN_DEVICE env var. Defaults to "privateuseone:0"
            (DirectML on the first GPU — works on AMD/Intel/NVIDIA on
            Windows). Pass "cuda" if you have NVIDIA + CUDA torch
            installed instead, or "cpu" to force CPU. Pass None to let
            terrain-diffusion's auto-select run (cuda if available else
            cpu — note this WON'T pick DirectML, which is why the
            default is explicit).
        repo_dir: filesystem path to the cloned terrain-diffusion repo.
        venv_dir: filesystem path to the Python venv with inference
            deps installed. Defaults to `<repo_dir>/.venv`. See
            CLAUDE.md "Terrain-diffusion runtime" for the install
            recipe.
        decoder_tile_size: spatial tile size for the decoder stage.
            Default 128 (fits 8 GB VRAM). Raise to 256-512 if you have
            more headroom (16+ GB) for faster, less-seamed output.
            **Must pair with a smaller `decoder_tile_stride`**.
        decoder_tile_stride: how far each decoder tile advances. Must
            be <= decoder_tile_size to ensure overlap. Default 96
            (75% of tile_size=128). Larger stride = faster but more
            visible seams. Smaller = slower, smoother.
        batch_size: latent-stage batch size. Default 1 (lowest VRAM).
            Upstream default is "1,4" which can OOM on 8 GB cards.
        no_compile: pass `--no-compile`. torch.compile is a no-op on
            Windows anyway (per upstream warning) and not setting this
            can cause subtle init issues. Default True.
        dtype: model dtype. Default "fp32" (most stable). "bf16"/"fp16"
            cut VRAM in half but DirectML support on older AMD cards
            (RX 5000 series) varies.
        drop_water_pct: conditioning bias. 0.0 = unbiased, model picks
            water/land per seed. 0.5 = upstream default (more land).
            Raise toward 1.0 to bias toward land-only output. Defaults
            to 0.0 here for predictability.
        seed: world seed for reproducibility. None = random. Note: the
            running server's seed cannot be changed via HTTP (there is
            no POST /seed endpoint despite stale docs); to change seed,
            restart with a different value here.
        extra_kwargs: additional `--kwarg key=value` pairs to pass
            through to the WorldPipeline constructor.
        wait_for_ready: poll the server until it responds before returning.
        ready_timeout: seconds to wait for readiness.

    Returns: dict with PID, server URL, and readiness status. The process
    keeps running until you call stop_terrain_diffusion_server() or kill
    it manually."""
    # Validate the paired tile/stride invariant
    if decoder_tile_stride > decoder_tile_size:
        raise ValueError(
            f"decoder_tile_stride ({decoder_tile_stride}) must be <= "
            f"decoder_tile_size ({decoder_tile_size}). Stride > size "
            "produces all-NaN model output silently (no tile overlap, "
            "seam-blending math diverges)."
        )
    if not os.path.isdir(repo_dir):
        raise FileNotFoundError(
            f"terrain-diffusion repo not found at {repo_dir!r}. "
            f"Clone it first:\n"
            f"  git clone https://github.com/xandergos/terrain-diffusion {repo_dir}"
        )

    if venv_dir is None:
        venv_dir = os.path.join(repo_dir, ".venv")
    # Cross-platform venv python resolution
    venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
    if not os.path.isfile(venv_python):
        # POSIX fallback
        alt = os.path.join(venv_dir, "bin", "python")
        if os.path.isfile(alt):
            venv_python = alt
        else:
            raise FileNotFoundError(
                f"Venv python not found at {venv_python!r}. "
                f"Create the venv first (see CLAUDE.md \"Terrain-diffusion "
                f"runtime\" section) — roughly:\n"
                f"  py -3.10 -m venv {venv_dir}\n"
                f"  {venv_dir}/Scripts/python.exe -m pip install torch-directml \\\n"
                f"      diffusers accelerate flask click h5py matplotlib \\\n"
                f"      scikit-image scipy infinite-tensor safetensors \\\n"
                f"      ema-pytorch tqdm pyyaml pyfastnoiselite numba \\\n"
                f"      huggingface_hub rasterio"
            )

    # Run our launcher script (NOT `python -m terrain_diffusion.inference.api`)
    # so we can pre-import torch_directml when DirectML is requested. Without
    # that pre-import, `tensor.to("privateuseone:0")` raises
    # `ModuleNotFoundError: No module named 'torch.privateuseone'`. The
    # launcher also avoids the heavy `terrain_diffusion/__main__.py` which
    # imports training-only modules (cartopy, earthengine-api, optuna,
    # wandb) at top level.
    launcher = os.path.join(
        os.path.dirname(__file__), "_terrain_diffusion_launcher.py"
    )
    cmd = [
        venv_python, launcher, model,
        "--port", str(port),
        "--batch-size", str(batch_size),
        "--dtype", dtype,
    ]
    if device:
        cmd.extend(["--device", device])
    if no_compile:
        cmd.append("--no-compile")
    if seed is not None:
        cmd.extend(["--seed", str(seed)])
    # WorldPipeline kwargs go through `--kwarg key=value` (parsed by
    # parse_kwargs in api.py). The tile_size/stride pair is the critical
    # one — see the docstring's "VRAM tuning" note.
    pipeline_kwargs: dict = {
        "decoder_tile_size": decoder_tile_size,
        "decoder_tile_stride": decoder_tile_stride,
        "drop_water_pct": drop_water_pct,
    }
    if extra_kwargs:
        pipeline_kwargs.update(extra_kwargs)
    for k, v in pipeline_kwargs.items():
        cmd.extend(["--kwarg", f"{k}={v}"])

    # Belt-and-suspenders: also set TERRAIN_DEVICE env so even paths that
    # don't honor --device pick up the device choice (api.py:_select_device
    # checks the env var first).
    env = os.environ.copy()
    if device:
        env["TERRAIN_DEVICE"] = device

    # The terrain-diffusion package isn't pip-installed in the venv — it's
    # just a clone. When we run our launcher script directly, sys.path[0]
    # gets the launcher's directory (bo3_mcp/), not the terrain-diffusion
    # repo. Add the repo to PYTHONPATH so `import terrain_diffusion` resolves.
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        repo_dir + (os.pathsep + existing_pp if existing_pp else "")
    )

    proc = subprocess.Popen(
        cmd,
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

    result = {
        "pid": proc.pid,
        "server_url": f"http://localhost:{port}",
        "model": model,
        "repo_dir": repo_dir,
        "venv_python": venv_python,
        "device": device,
        "ready": False,
    }

    if not wait_for_ready:
        result["note"] = (
            "Server started in background. Poll /seed endpoint manually to "
            "check readiness."
        )
        return result

    # Poll /health until it responds (200 OK). The live API exposes only
    # /health and /terrain — the older API_README mentions /seed but that
    # endpoint was removed.
    start = time.time()
    url = f"http://localhost:{port}/health"
    while time.time() - start < ready_timeout:
        if proc.poll() is not None:
            # Process died before ready
            out = ""
            if proc.stdout:
                try:
                    out = proc.stdout.read()
                except Exception:
                    pass
            raise RuntimeError(
                f"terrain-diffusion server exited early "
                f"(returncode={proc.returncode}). Output:\n{out[-2000:]}"
            )
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status == 200:
                    result["ready"] = True
                    result["elapsed_seconds"] = round(time.time() - start, 1)
                    return result
        except (urllib.error.URLError, ConnectionResetError, TimeoutError):
            pass
        time.sleep(2.0)

    result["note"] = (
        f"Server didn't respond within {ready_timeout}s — may still be "
        f"loading model weights (first run takes 1-3 min). Check the "
        f"process output, or retry with wait_for_ready=False and poll "
        f"manually."
    )
    return result
