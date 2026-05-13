# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP (Model Context Protocol) server that lets Claude author Black Ops III custom zombie maps end-to-end: scaffold project files, place entities (perks, mystery box, pack-a-punch, wall buys, spawners, zones, doors, barricades), synthesize axis-aligned brush geometry, edit GSC scripts, then compile and link via Treyarch's official mod tools. README.md is the user-facing reference and tool catalog; this file covers what's non-obvious for working *on* the code.

## Install / run / register

```powershell
pip install -e .                                          # editable install
python -m bo3_mcp.server                                  # run the stdio server (manual smoke test)
claude mcp add --scope user bo3 -- python -m bo3_mcp.server   # register with Claude Code
claude mcp list                                           # verify
```

There are no tests, linters, or formatters configured. The MCP itself has a single runtime dep (`mcp>=1.0.0`) and targets Python 3.11+. **The terrain-diffusion feature has its own isolated venv** — see "Terrain-diffusion runtime" below.

`BO3_MOD_TOOLS` env var overrides the hardcoded install path (defaults to `D:\Steam\steamapps\common\Call of Duty Black Ops III 455130` — see [bo3_mcp/paths.py:9](bo3_mcp/paths.py:9)). Everything the server writes lives **outside** this repo, under `<MOD_TOOLS>/map_source/`, `<MOD_TOOLS>/share/raw/scripts/zm/`, and `<MOD_TOOLS>/usermaps/`.

## Terrain-diffusion runtime

The `generate_terrain_diffusion` tool path (real ML-driven terrain, not the value-noise placeholder in `generate_terrain`) calls out to a separate Flask REST server backed by [xandergos/terrain-diffusion](https://github.com/xandergos/terrain-diffusion). That server lives in its **own venv** at `D:\projects\terrain-diffusion\.venv` (Python 3.10 + torch 2.4.1 + torch-directml + diffusers) — separate from the MCP's Python 3.14 because `torch-directml` hard-pins `torch==2.4.1` which has no 3.14 wheels.

Three MCP tools, three purposes:
- `generate_terrain` → value-noise placeholder. **Not** terrain-diffusion. Useful baseline, no setup needed. The name is misleading; rename candidate `generate_terrain_noise`.
- `preview_terrain_diffusion_region` → non-mutating probe. Hits the server, returns elev/climate stats + brush-count estimates + tuning recommendations. **Call this first** when scouting; cheap (~6s/call on GPU).
- `generate_terrain_diffusion` → real model output via HTTP, emits brushes. Supports `seed` (passed as `/terrain?seed=N` — no server restart), `normalize_elevation=True` (uses local min as effective sea level so bathymetric regions become usable relief), `floor_thickness_units` (solid ground under the lowest cell), `allow_constant` (override the all-zero refuse guard). Refuses to emit 0 brushes with an actionable error.

**Negative elevation is valid** — the model's output is real-world meters, and most of the world is ocean (-1000m+). Don't try to filter for positive-only regions; instead pass `normalize_elevation=True` and the bathymetric variation becomes BO3 relief. Use `preview_terrain_diffusion_region` to see the `recommendations` (sea_level / world_units_per_meter / normalize-suggested) before generating.

**One-time setup** (≈3 GB on disk, half is torch):
```powershell
# 1. Clone the repo
git clone https://github.com/xandergos/terrain-diffusion D:\projects\terrain-diffusion

# 2. Build a Python 3.10 venv inside it
py -3.10 -m venv D:\projects\terrain-diffusion\.venv

# 3. Install torch-directml (pulls torch 2.4.1 + torchvision 0.19.1) + inference deps
D:\projects\terrain-diffusion\.venv\Scripts\python.exe -m pip install `
  torch-directml diffusers accelerate flask click h5py matplotlib `
  scikit-image scipy infinite-tensor safetensors ema-pytorch tqdm `
  pyyaml pyfastnoiselite numba huggingface_hub rasterio

# 4. WorldClim climate data (the synthetic_map factory loads this — required at server boot)
$dataDir = "D:\projects\terrain-diffusion\data\global"
mkdir -Force $dataDir | Out-Null
curl -L -o "$dataDir\wc2.1_10m_bio.zip" https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_10m_bio.zip
Expand-Archive "$dataDir\wc2.1_10m_bio.zip" -DestinationPath $dataDir
# etopo_10m.tif is already shipped with the repo's data/global/ dir
```

**GPU on AMD**: torch-directml gives DirectX 12-backed acceleration on AMD/Intel GPUs on Windows. After install, verify with:
```powershell
D:\projects\terrain-diffusion\.venv\Scripts\python.exe -c "import torch, torch_directml; print(torch_directml.device_name(0))"
```

**Local mods to terrain-diffusion** (necessary, not committed upstream — third-party repo):
- `terrain_diffusion/models/mp_layers.py:resample` — when `x.device.type == 'privateuseone'`, substitute `F.interpolate(mode='nearest')` for `conv_transpose2d(ones-kernel, groups=c)`. DirectML doesn't implement depthwise transposed conv. The substitution is mathematically bit-equivalent because the original kernel is all-ones with stride == kernel_size (no overlap).

**Launcher shim** ([bo3_mcp/_terrain_diffusion_launcher.py](bo3_mcp/_terrain_diffusion_launcher.py)): `torch_directml` registers its `privateuseone` backend **lazily**. If anything calls `.to('privateuseone:0')` before `import torch_directml`, you get `ModuleNotFoundError: No module named 'torch.privateuseone'`. The launcher pre-imports `torch_directml` when DirectML is requested, then hands off to `terrain_diffusion.inference.api.main()`. `start_terrain_diffusion_server` in [terrain.py](bo3_mcp/terrain.py) invokes the launcher with `PYTHONPATH=<repo_dir>` (the terrain-diffusion package isn't pip-installed, just cloned), `cwd=<repo_dir>`, and `TERRAIN_DEVICE` env var set.

**Memory tuning + the critical tile/stride pairing**: 8 GB VRAM (RX 5700) won't fit the upstream default `decoder_tile_size=512` at fp32 — gets ~128 MB allocation failures partway through the decoder. We default to `decoder_tile_size=128`. **CRITICAL**: when you change `decoder_tile_size`, you MUST also set `decoder_tile_stride <= decoder_tile_size`. If stride > size, decoder tiles don't overlap and the seam-blending math diverges to all-NaN output, which then silently casts to int16=0 (looking like "flat ocean"). The MCP-side `start_terrain_diffusion_server` defaults to `decoder_tile_size=128, decoder_tile_stride=96` together and **hard-fails** if you pass a bad combo. Don't manually pass only one of the pair.

**Endpoints**: only `/health` (readiness) and `/terrain` (data). The old API_README mentions a standalone `/seed` endpoint but it doesn't exist — however, `/terrain?seed=N` **does** work (upstream `terrain()` handler reads `seed` and calls `world.change_seed(seed)` server-side, clearing the cache). So MCP code passes `seed` as a query param; no server restart needed to change seeds. `start_terrain_diffusion_server`'s readiness poll uses `/health`.

## Dev loop: hot-reload, no restart

Restarting the MCP server requires restarting Claude Desktop, which is slow. Instead, after editing any `bo3_mcp/*.py` submodule, call the MCP tool `_reload_mcp_modules` — it re-imports the submodules in topological order so subsequent tool calls pick up the edits. The leaves-first reload order lives in [server.py:40](bo3_mcp/server.py:40). `server.py` itself is **not** reloadable (would re-register tools and break FastMCP); only its `from . import zm`-style references are late-bound, so changes to submodules surface through.

## Architecture

Bottom-up dependency stack — each layer only imports from layers above it in this list:

```
paths.py        BO3 install root + path helpers + build subprocess env vars
mapfile.py      iwmap 4 text-format parser/writer (entities structured, brushes opaque)
brushes.py      axis-aligned box brush synthesis (inward-normal winding!)
textures.py     vetted material catalog (extracted from real zm_giant brush faces)
entities.py     generic entity CRUD on a MapFile (GUID-keyed)
gsc.py          GSC line edits: #using auto-management, init_zones[N] append, default_start_location
geometry.py     rooms / walls / slabs / stairs / doorways / courtyards / exterior seal
terrain.py      heightmap → voxelized box-brush terrain (+ optional terrain-diffusion API client)
zm.py           zombie-mode recipes: perks/box/pap/wall buys/spawners/zones/doors/barricades + catalogs
scaffold.py     create_zombie_map + GSC/CSC/.map/.zone templates
pipeline.py     subprocess wrappers for gdtdb / cod2map64 / radiant_modtools / linker_modtools + output parsing
demo.py        one-call demo map (composes the recipes above)
server.py       FastMCP stdio entry; thin shims around the modules above, ~40 tools
```

### Two key design choices

**1. Brushes are opaque text; entities are structured.** [mapfile.py](bo3_mcp/mapfile.py) parses entity blocks into `Entity(guid, layer, kvps, brushes)` but keeps each brush as a raw text block. The serializer rewrites the `// brush N` header on save but preserves the body verbatim. This means brush geometry can come from Radiant or `brushes.box_brush()` and round-trip safely — we cannot accidentally corrupt geometry by re-emitting it. The trade-off: we can only *append* brushes from code, not *modify* existing ones.

**2. Most ZM placements are `misc_prefab` references into Treyarch's stock library.** Helpers in [zm.py](bo3_mcp/zm.py) reduce to: pick the right per-concern prefab file (vending / magicboxes / weapons), add a `misc_prefab` entity whose `model` KVP points at `_prefabs/zm/zm_core/<thing>.map`. The scaffolded top-level `.map` already includes `misc_prefab` references to the per-map prefab files, so the framework auto-loads them. This is why `add_perk` is roughly 30 lines.

### File-locality conventions

When mutating something, the **right file to load** depends on the entity type — these are not all stored in one `.map`:

| Helper | Writes to |
|---|---|
| `add_perk` / `add_pack_a_punch` | `_prefabs/zm/<map>/script/<map>_vending.map` |
| `add_mystery_box` | `_prefabs/zm/<map>/script/<map>_magicboxes.map` |
| `add_wall_buy` | `_prefabs/zm/<map>/script/<map>_weapons.map` |
| `add_player_spawn` / `add_power_switch` / zones / spawners / doors / barricades / decals / brushes | top-level `map_source/zm/<map>.map` |
| `add_perk` / `add_pack_a_punch` / `add_zombie_zone` / `add_buyable_door(connects=...)` | **also edits the `.gsc`** (auto-manages `#using` imports, appends `init_zones[N]`, updates `default_start_location`, inserts `add_adjacent_zone`) |

The path helpers in [paths.py](bo3_mcp/paths.py) (`map_source`, `map_prefab_dir`, `core_prefab`, `gsc`, `csc`, `zone_manifest`) are the single source of truth for where things live — use them rather than building paths by hand.

### Brush winding convention

Treyarch's `iwmap 4` brush format uses **inward-normal winding** — the cross product of a face's three points `(B-A) x (C-A)` points INTO the solid, opposite the standard Quake convention. Verified empirically against `_prefabs/zm/zm_core/buyable_magic_box_start.map`. The face-points table lives in [brushes.py:62](bo3_mcp/brushes.py:62). If a future tools update flips this, that's the function to edit.

### Build pipeline

[pipeline.py](bo3_mcp/pipeline.py) shells out to four executables. Critical details captured by reverse-engineering the Mod Tools Launcher's Output Window during a real `zm_giant` build:

- All subprocess calls set `cwd=bin/` and the `TA_GAME_PATH` / `TA_LOCAL_ASSET_CACHE` / `TA_TOOLS_PATH` env vars from `paths.build_env()`. Without the bin/ cwd, `cod2map64`'s `FS_Startup` fails to resolve its relative search paths and navmesh generation breaks silently.
- Full compile (`only_ents=False`) must pass `-navmesh -navvolume` or zombies have no AI pathing.
- `parse_warnings` (the build-output post-processor) extracts: generic warnings/errors, per-asset xmesh warnings, **missing materials** (recurrent issue when texture names drift), the linker's "N errors / M warnings" summary, and the **leak detection banner** (`****** leaked ******` + `.lin` leakfile path). Leaks return `returncode 0` so silent leaks would otherwise pass; we surface them in the `summary.leak` block.
- `bake_lighting` (radiant_modtools) uses Qt-style `+flag` syntax (not `-flag`), and the map path is positional. Worked out by comparing launcher invocations to ours.

### Zombie-map gotchas baked into the helpers

- **Map names must start with `zm_`** — enforced in `scaffold.create_zombie_map`.
- **Spawners must have `targetname "<zone>_spawners"`** to be discovered by the framework — spatial overlap is not enough. `add_zombie_spawner(zone_name=...)` sets this; without `zone_name` no zombies appear in that zone.
- **Barricades leak.** Their prefab places `exterior_goal` and `zbarrier` entities ~50 units beyond the wall, which sit in unbounded void in synthesized maps. `seal_exterior` wraps the playable area in 6 caulk slabs to make the void finite. `add_lighting_kit` does this plus sun/umbra/probe in one call. Maps with barricades **need** one of these called before compile.
- **Umbra volume is required.** Without `umbra_volume`, cod2map64 defaults to a million-unit cube and the resulting BSP renders walls as the skybox texture in-game.
- The GSC scaffold ships `init_zones = [];` *empty* (v1.0 change). The first `add_zombie_zone` call lands cleanly at `init_zones[0]`. Don't reintroduce the placeholder.

## Adding a new MCP tool

The pattern: implement the logic in the appropriate submodule (`zm.py` for ZM recipes, `geometry.py` for brush ops, `pipeline.py` for build steps), then add a thin `@mcp.tool()` shim in [server.py](bo3_mcp/server.py) that takes JSON-friendly types (`list[float]` instead of tuples), converts via `_xyz()`, calls the module function, and returns its dict. The docstring is what Claude reads when picking the tool — write for planning context (what it does, when to use vs. alternatives, common gotchas), not just API reference. After editing, call `_reload_mcp_modules` from chat to pick up the change.

## Coordinate / unit conventions

World units are inches. Z is up. Origin format is `[x, y, z]`. Angles default to `[0, 0, 0]`. The `+y` direction is "north" in the brush-side naming used by `carve_room_with_openings` and `add_outdoor_courtyard` (`side: "south"|"north"|"east"|"west"`).
