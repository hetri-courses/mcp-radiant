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

There are no tests, linters, or formatters configured. Single runtime dep is `mcp>=1.0.0`. Python 3.11+.

`BO3_MOD_TOOLS` env var overrides the hardcoded install path (defaults to `D:\Steam\steamapps\common\Call of Duty Black Ops III 455130` — see [bo3_mcp/paths.py:9](bo3_mcp/paths.py:9)). Everything the server writes lives **outside** this repo, under `<MOD_TOOLS>/map_source/`, `<MOD_TOOLS>/share/raw/scripts/zm/`, and `<MOD_TOOLS>/usermaps/`.

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
