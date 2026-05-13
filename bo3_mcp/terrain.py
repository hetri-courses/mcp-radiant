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
    server_url: str = DEFAULT_TD_SERVER,
    timeout: float = 180.0,
) -> tuple[list[list[float]], dict]:
    """Hit the xandergos/terrain-diffusion REST API and return a heightmap
    region in METERS, plus parsed climate channels.

    The server must be running locally — start it with:
        python -m terrain_diffusion api xandergos/terrain-diffusion-30m

    See https://github.com/xandergos/terrain-diffusion for install +
    weight-download instructions. The server binds to localhost:8000 by
    default.

    Args:
        i1, j1, i2, j2: bounding box in target-resolution pixel coords.
            (i,j) = (x,y) — `i1,j1` is top-left, `i2,j2` is bottom-right.
            Width = i2-i1, height = j2-j1. Each pixel covers `90/scale`
            meters on the 90m model, `30/scale` on the 30m model.
        scale: integer multiplier vs base resolution. 1=base, 2=2x, 4=4x.
        server_url: base URL of the terrain-diffusion REST API server.
        timeout: HTTP timeout in seconds. Diffusion sampling on CPU can
            take minutes for large regions.

    Returns:
        (heightmap, meta) where:
        - heightmap: 2D list of floats, dimensions (j2-j1, i2-i1). Values
          are elevation in METERS (floored, signed). Negative values are
          ocean/below-sea-level. Pass to `heightmap_to_brushes` with an
          appropriate `height_scale` to convert to BO3 world units.
        - meta: dict with `width`, `height`, `n_climate_channels`, raw
          `climate` bytes (for advanced use).

    Raises ConnectionError if the server isn't reachable — typically means
    you haven't started it, or it's still loading the model weights."""
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
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"Couldn't reach terrain-diffusion API at {server_url}: {e}. "
            "Did you start the server? Try:\n"
            "  python -m terrain_diffusion api xandergos/terrain-diffusion-30m\n"
            "Then wait for model loading to finish before retrying."
        ) from e

    # Response layout:
    #   elevation: int16 little-endian, height*width*2 bytes
    #   climate:   float32 little-endian interleaved (h, w, 4 channels)
    elev_bytes = height * width * 2
    if len(data) < elev_bytes:
        raise ValueError(
            f"server returned {len(data)} bytes but elevation alone needs "
            f"{elev_bytes}. Did the request fail silently?"
        )
    heightmap: list[list[float]] = []
    for y in range(height):
        row_offset = y * width * 2
        row = list(struct.unpack_from(f"<{width}h", data, row_offset))
        heightmap.append([float(v) for v in row])

    climate_bytes = data[elev_bytes:]
    return heightmap, {
        "width": width,
        "height": height,
        "elev_byte_count": elev_bytes,
        "climate_byte_count": len(climate_bytes),
        "n_climate_channels": 4,  # temp, t_season, precip, p_cv
        "climate_raw": climate_bytes,
        "min_elev_m": min(min(r) for r in heightmap),
        "max_elev_m": max(max(r) for r in heightmap),
    }


def generate_terrain_diffusion(
    map_name: str,
    *,
    region: tuple[int, int, int, int] = (0, 0, 128, 128),
    scale: int = 1,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    cell_size: float = 64.0,
    sea_level_m: float = 0.0,
    world_units_per_meter: float = 0.3,
    server_url: str = DEFAULT_TD_SERVER,
    height_bands: list[tuple[float, str]] | None = None,
    merge_strips: bool = True,
    max_brushes: int = 32768,
) -> dict:
    """Generate BO3 terrain using xandergos/terrain-diffusion as the heightmap
    source. Hits the REST API, converts meters → BO3 inches, places brushes.

    Args:
        region: (i1, j1, i2, j2) pixel-coordinate bbox in the model's
            output space. (j2-j1) = grid rows, (i2-i1) = grid columns.
            Default 128×128 = 16384 cells, fast to sample but plenty
            detailed.
        scale: resolution multiplier (1 = base, 2 = 2x, 4 = 4x).
        origin: world position of the (i1, j1) corner in BO3 units.
            Z is the base elevation that maps to sea_level_m.
        cell_size: XY extent per heightmap pixel in BO3 units.
        sea_level_m: meters from the model's output that map to origin.z
            in world units. Elevations below this become void (no brush).
        world_units_per_meter: how many BO3 inches one meter of real-world
            elevation maps to. 0.3 = scaled-down realistic mountains
            (a 100m hill becomes 30 BO3 units). 1.0 ≈ 1:1 but most BO3
            elevations are tens of meters tall, so 0.3-0.5 reads well.
        server_url: terrain-diffusion API URL (default localhost:8000).
        height_bands: per-elevation textures `[(top_z, "tex"), ...]`.
            Defaults to a wasteland palette.
        merge_strips: collapse same-height X-runs (recommended True).
        max_brushes: safety budget.

    Returns: dict from heightmap_to_brushes plus terrain-diffusion meta
    (min/max elevation, climate channel counts)."""
    i1, j1, i2, j2 = region
    heightmap_m, meta = fetch_terrain_diffusion_region(
        i1, j1, i2, j2, scale=scale, server_url=server_url
    )

    # Convert meters → BO3 units, subtract sea level, clamp negatives → 0.
    scaled: list[list[float]] = []
    for row in heightmap_m:
        scaled_row = []
        for m in row:
            world_z = (m - sea_level_m) * world_units_per_meter
            scaled_row.append(max(0.0, world_z))
        scaled.append(scaled_row)

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
        "min_elev_m": meta["min_elev_m"],
        "max_elev_m": meta["max_elev_m"],
        "world_units_per_meter": world_units_per_meter,
        "sea_level_m": sea_level_m,
    }
    return result


def start_terrain_diffusion_server(
    *,
    model: str = "xandergos/terrain-diffusion-30m",
    port: int = 8000,
    device: str | None = None,
    repo_dir: str = "D:/projects/terrain-diffusion",
    wait_for_ready: bool = True,
    ready_timeout: float = 300.0,
) -> dict:
    """Spawn the terrain-diffusion REST API server as a background process.

    First-run takes 1-3 minutes (model weights download from Hugging Face
    Hub + loading into VRAM). Subsequent runs are seconds.

    Args:
        model: HF model identifier. Choices the repo ships:
            - xandergos/terrain-diffusion-30m (recommended for games)
            - xandergos/terrain-diffusion-90m (realistic worldbuilding)
        port: where the Flask server binds.
        device: "cuda" / "cpu" / None (auto). CPU works but is much
            slower (minutes per region instead of seconds).
        repo_dir: filesystem path to the cloned terrain-diffusion repo.
        wait_for_ready: poll the server until it responds before returning.
        ready_timeout: seconds to wait for readiness.

    Returns: dict with PID, server URL, and readiness status. The process
    keeps running until you call stop_terrain_diffusion_server() or kill
    it manually."""
    if not os.path.isdir(repo_dir):
        raise FileNotFoundError(
            f"terrain-diffusion repo not found at {repo_dir!r}. "
            f"Clone it first:\n"
            f"  git clone https://github.com/xandergos/terrain-diffusion {repo_dir}\n"
            f"  cd {repo_dir} && pip install -r requirements.txt"
        )

    cmd = [
        sys.executable, "-m", "terrain_diffusion", "api", model,
        "--port", str(port),
    ]
    if device:
        cmd.extend(["--device", device])

    proc = subprocess.Popen(
        cmd,
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    result = {
        "pid": proc.pid,
        "server_url": f"http://localhost:{port}",
        "model": model,
        "repo_dir": repo_dir,
        "ready": False,
    }

    if not wait_for_ready:
        result["note"] = (
            "Server started in background. Poll /seed endpoint manually to "
            "check readiness."
        )
        return result

    # Poll the /seed endpoint until it responds (200 OK with JSON).
    start = time.time()
    url = f"http://localhost:{port}/seed"
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
