# bo3-mcp

An MCP server for authoring Black Ops III custom **zombie maps** by talking to Claude. Drop perks, mystery boxes, pack-a-punch, wall buys, player spawns, and zone markers from chat. Compile and link via the official mod tools, no GUI required for those steps.

## What it does (and doesn't)

| Concern | Owned by MCP | Owned by Radiant |
|---|---|---|
| Entity placement (perks, box, pap, wall buys, switches, spawns) | ✅ | |
| Zone markers (the entity) | ✅ | brush bounds |
| GSC scaffolding (main, zone init, zone manager) | ✅ | |
| Zone manifest (`.zone` asset list) | ✅ | |
| Compile (`cod2map64.exe`) + Link (`linker_modtools.exe`) | ✅ | |
| Axis-aligned box brushes (rooms, walls, floors, stairs, doorways) | ✅ (v0.5) | |
| ML terrain (terrain-diffusion → patch meshes), prop scatter | ✅ (v23) | biome blend reverted; minor residual foliage float on steep cells (accepted) |
| Hand-sculpted curved/complex geometry | | ✅ |
| Lighting placement, light export, real lighting build | | ✅ |
| In-game playtest launch | | ✅ (via launcher) |

> **v23 terrain architecture (May 2026):** the MCP now owns ML-driven
> terrain end-to-end — `make_terrain_zombie_arena` generates a
> diffusion heightmap, renders it as smooth BO3 `mesh` patches (NOT
> blocky voxel brushes), and scatters stock foliage/debris.
> `terrain_height_at_xy` is bilinear (props/perks/spawners sit exactly
> on the interpolated surface); foliage gets a slope-aware sink so it
> reads as planted on broken_floor slopes — a minor residual float on
> the steepest cells is a known, accepted limitation. The v23.18-19
> grass-floor biome blend was **reverted** (never read right in-game);
> the infra is left inert. `terrain_preset` = `outdoor_rugged` /
> `indoor_subtle`. Architecture, hard-won format rules, and
> verified-pattern invariants live in **CLAUDE.md** ("Terrain
> rendering: patches over voxel", "Prop scatter", "Terrain presets")
> and `tests/fixtures/PATCH_FORMAT_NOTES.md`.

## Setup

### Prerequisites

- BO3 Mod Tools installed via Steam → Tools (puts everything at `D:\Steam\steamapps\common\Call of Duty Black Ops III 455130\` on this machine)
- Python 3.11 or later
- Claude Code (or any MCP-capable client)

### Install

```bash
cd D:\projects\bo3-mcp
pip install -e .
```

Or just install the runtime dep without an editable install:

```bash
pip install mcp
```

If your mod tools live somewhere other than the path above, set `BO3_MOD_TOOLS` in your environment:

```powershell
$env:BO3_MOD_TOOLS = "E:\my\custom\path\Call of Duty Black Ops III 455130"
```

### Wire it into Claude Code

The MCP server registry lives at `~/.claude.json` (not `settings.json` — that file controls flags/permissions/hooks; MCP servers are separate). Easiest way to register is the CLI:

```bash
# Install the package editable so `python -m bo3_mcp.server` works from any cwd
pip install -e D:\projects\bo3-mcp

# Register the server in user scope
claude mcp add --scope user bo3 -- python -m bo3_mcp.server

# Verify
claude mcp list
# bo3: python -m bo3_mcp.server - ✓ Connected
```

Restart Claude Code. Run `/mcp` — you should see `bo3` connected with all the tools.

If you'd rather hand-edit `~/.claude.json`, the entry under `mcpServers` looks like:

```json
"bo3": {
  "type": "stdio",
  "command": "python",
  "args": ["-m", "bo3_mcp.server"]
}
```

## Workflow

A custom zombie map's lifecycle from zero to playable:

```
1. Scaffold        →  one MCP call, creates 7 files
2. Geometry        →  Radiant (sculpt rooms, doorways, geometry detail)
3. Entities        →  MCP (perks, box, pap, spawns, zone markers, wall buys)
4. GSC tweaks      →  MCP file edits or hand-edit the .gsc
5. Compile + Link  →  MCP (cod2map64 + linker_modtools)
6. Lighting        →  Radiant ("Light" checkbox in launcher) — only when ready
7. Playtest        →  Mod Tools Launcher → Run
```

Steps 1, 3, 4, 5 are fully driven from chat. Step 2 you stay in Radiant. Steps 6 and 7 use the launcher GUI.

### Step 1 — Scaffold

> *"Scaffold a zombie map called `zm_parlor`."*

The MCP creates:

```
map_source/zm/zm_parlor.map                            ← top-level (open in Radiant)
map_source/_prefabs/zm/zm_parlor/script/zm_parlor_vending.map     ← perks land here
map_source/_prefabs/zm/zm_parlor/script/zm_parlor_magicboxes.map  ← mystery boxes
map_source/_prefabs/zm/zm_parlor/script/zm_parlor_weapons.map     ← wall buys
share/raw/scripts/zm/zm_parlor.gsc                     ← server logic
share/raw/scripts/zm/zm_parlor.csc                     ← client-side
usermaps/zm_parlor/zone_source/zm_parlor.zone          ← asset manifest
```

Map names **must** start with `zm_`.

### Step 2 — Geometry (MCP for boxy stuff, Radiant for the rest)

For axis-aligned shapes — rooms, walls, floors, ceilings, stairs, doorways, debris piles — use the MCP. For curved walls, terrain, custom angles, or anything you'd want to *see* before committing, use Radiant.

> *"Carve a 512×512×256 starter room centered at origin, walls 16 thick."*
> *"Add a 256×256×128 hallway from `[256, 0, 0]` to `[768, 256, 256]`."*
> *"Add a 4-step staircase up to the second floor at `[100, 200, 16]`."*
> *"Punch a 64×96 doorway in the east wall of the starter room."*

Coordinates are world units (inches). Z is up. Default wall thickness is 16, default texture is `t7_concrete_trowelled` — you can override per call.

If you'd rather sculpt visually, open `map_source/zm/zm_parlor.map` in Radiant (`Radiant_modtools.exe` from `bin/`, or via the launcher's Level Editor button) and draw brushes there. The MCP and Radiant edit the same file — you can switch back and forth freely.

### Step 3 — Entities (MCP)

Coordinates use BO3's world units (inches). Z is up. Origin format is `[x, y, z]`. Defaults to angles `[0, 0, 0]` if you don't pass them.

> *"Place juggernog at `[100, 50, 32]`."*
> *"Add the mystery box at `[300, 0, 32]` and pack-a-punch at `[-200, 200, 32]`."*
> *"Drop a power switch at `[-400, 0, 32]`."*
> *"Add wall buy `smg_standard` at `[150, 100, 64]`."*
> *"Add a zone marker called `start_zone` at `[0, 0, 32]` with size `[512, 512, 256]`."*

Behind the scenes, perks/box/pap/wall-buys go into the per-concern prefab files (already referenced from the top-level via `misc_prefab` entities, so they auto-load). Power switch, player spawns, and zone markers go into the top-level `.map`.

To inspect the current state:

> *"List all entities in `zm_parlor`."*
> *"Show all `misc_prefab` entities in `zm_parlor`."*
> *"Find anything within 100 units of `[0, 0, 32]`."*
> *"Find the entity with targetname `initial_spawn_points`."*

To modify or remove:

> *"Move the entity with guid `{...}` to `[150, 0, 32]`."*
> *"Update the entity at guid `{...}` — set `script_string` to `juggernog`."*
> *"Delete the entity with guid `{...}`."*

### Step 4 — GSC tweaks

The scaffolded `.gsc` is a working minimum: calls `zm_usermap::main()`, sets `default_game_mode = "zclassic"`, registers a single `start_zone`.

**Perk + pack-a-punch + zone registration are auto-managed.** Several things happen automatically when you call the corresponding helpers:

| Helper | What it auto-edits in the GSC |
|---|---|
| `add_perk` | Uncomments / inserts `#using scripts\zm\_zm_perk_<perk>;` |
| `add_pack_a_punch` | Uncomments / inserts `#using scripts\zm\_zm_pack_a_punch;` |
| `add_zombie_zone` | Appends `init_zones[N] = "<zone>";` (idempotent — duplicates are no-ops) |
| `add_zombie_zone(..., is_starting_zone=true)` | Also updates `level.default_start_location = "<zone>";` |
| `add_buyable_door(..., connects=[a, b])` | Inserts `zm_zonemgr::add_adjacent_zone("a", "b", "<flag>");` into the zone init function (idempotent in either zone order) |

What still needs hand-editing in the GSC:
- Connecting buyable doors to zone activation — listen for the door's `script_flag` in the gated zone's init function. v0.8 will automate this.
- Per-zone init function bodies (flag init for non-starter zones).
- Custom round logic, easter-egg steps, gamemode tweaks.

### Step 5 — Compile + Link

Two iteration modes:

**Fast path (entity changes only — seconds):**

> *"Compile and link `zm_parlor` with `only_ents=true` (default)."*

This skips geometry and lighting — perfect for tweaking entity placements after a Radiant edit. Note: `-onlyents` skips per-map prefabs too, so changes to your vending/magicboxes/weapons prefabs don't show up under `only_ents`.

**Full path (geometry / prefab / lighting changes — minutes):**

> *"Do a full compile and link of `zm_parlor`."*

The MCP will:
1. `gdtdb_update` — refresh asset DB
2. `compile_map` with `only_ents=false` — full BSP build
3. `link_map` — pack assets into `.ff` fastfiles

First build of any map runs ~5-10 minutes (asset conversion). Subsequent builds use the cache.

### Step 6 — Lighting

Stays in Radiant / launcher GUI:

1. In Radiant: hit the lightning-bolt button in the Camera View toolbar to bake lights, then File → Lighting Export.
2. Or in the Mod Tools Launcher: tick **Light** + **Build**.

Lighting needs Radiant's renderer, so the MCP doesn't drive it.

### Step 7 — Playtest

Mod Tools Launcher → tick your map → tick **Run** → Build. Or in-game console: `devmap zm_parlor`.

## Tool reference

### Inspection

- `list_entities(map_name, classname=None)` — list entities in the top-level map, optionally filtered by classname.
- `find_entities_near(map_name, origin, max_distance, classname=None)` — spatial search, sorted by distance.
- `find_by_targetname(map_name, targetname)` — exact match on `targetname` KVP.
- `get_entity(map_name, guid)` — full KVP dict for a single entity.

### Editing

- `update_entity_kvps(map_name, guid, kvps)` — patch KVPs (pass `null` value to delete a key).
- `move_entity(map_name, guid, origin)` — set origin.
- `delete_entity(map_name, guid)` — remove from top-level `.map`.

### Scaffolding

- `scaffold_zombie_map(name, overwrite=false)` — full directory tree + templates.

### Geometry

All operate on the top-level `<map>.map`'s worldspawn entity.

- `add_box_brush(map_name, mins, maxs, texture, face_textures=null)` — single solid axis-aligned box; the primitive everything else builds on. Pass `face_textures={"top": "caulk", ...}` to override per-face (sides: `bottom`, `top`, `south`, `north`, `east`, `west`). Common pattern: caulk on hidden faces for cleaner BSP.
- `add_floor(map_name, mins, maxs_xy, thickness=16, texture)` — slab spanning `mins` → `[max_x, max_y]` extending up by `thickness`.
- `add_wall(map_name, mins, maxs, texture)` — semantic alias for `add_box_brush`.
- `carve_room(map_name, mins, maxs, wall_thickness=16, texture, floor_texture=null, ceiling_texture=null)` — hollow box: floor + ceiling + 4 walls. Returns the void interior bounds (where you can safely place entities). **No doorways.**
- **`carve_room_with_openings(map_name, mins, maxs, openings, wall_thickness=16, ...)`** — same as `carve_room` but with pre-cut doorways/windows in one call. Each opening is a dict: `{"side": "south"|"north"|"east"|"west", "width": N, "height": N, "center_offset": N (default 0), "bottom": N (default 0 = on floor; >0 makes a window)}`. **Multiple openings per side are supported** — the wall is decomposed into a left fill + per-opening above/below sub-walls + between fills + right fill.
- `add_stairs(map_name, base_origin, step_count, step_depth=16, step_height=8, step_width=96, direction="+y", texture)` — stacked box brushes ascending in `direction` (`+x`/`-x`/`+y`/`-y`).
- `add_doorway_to_wall(map_name, wall_mins, wall_maxs, opening_mins, opening_maxs, texture)` — low-level: generates up to 4 sub-walls (above/below/left/right of the opening) given full wall extents and an opening rectangle. **Doesn't delete a pre-existing wall** — only useful when building walls one at a time. For most cases use `carve_room_with_openings`.

**Recommended room-building patterns:**

1. **Single call (most common):** `carve_room_with_openings` with all your doorways and windows specified up front. Compiles in one shot.
2. **Per-wall construction:** Skip `carve_room` entirely — call `add_floor` + `add_box_brush` for the ceiling + four `add_wall`/`add_doorway_to_wall` calls per side. Use this for >1 opening per wall (since `carve_room_with_openings` allows only one), or for irregular shapes.
3. **Carve-then-Radiant:** Use plain `carve_room` and finish doorway carving visually in Radiant.

### ZM placement

- `add_perk(map_name, perk, origin, angles=null)` — see `list_perks` for catalog.
- `add_pack_a_punch(map_name, origin, angles=null)`
- `add_mystery_box(map_name, origin, angles=null)` — first one added is the starting box.
- `add_power_switch(map_name, origin, angles=null)` — includes built-in `trigger_use`.
- `add_wall_buy(map_name, weapon, origin, angles=null)` — see `list_wall_weapons`.
- `add_player_spawn(map_name, origin, angles=null, player_slot=1)` — slots 1–4.
- `add_zombie_zone(map_name, zone_name, volume_center, volume_size, is_starting_zone=false)` — full zone helper: info_volume entity tagged with `script_noteworthy` + a synthesized `caulk` brush volume + auto-registers the zone in the GSC's `init_zones[]` (and as `default_start_location` if `is_starting_zone=true`). Idempotent. The `_zone` suffix is added automatically if you forget it.
- `add_zombie_spawner(map_name, origin, angles=null, count=9999)` — places an `actor_spawner_zm_factory_zombie` with the standard AI tuning KVPs. Zone association is by spatial overlap — make sure `origin` is inside an info_volume zone.
- `add_buyable_door(map_name, door_mins, door_maxs, cost, script_flag, connects=null, door_name=null, slide_vector=null, door_texture, trigger_inflate=8)` — 2-entity recipe: `script_brushmodel` for the door geometry that slides on purchase + `trigger_use` for the buy interaction. `script_flag` is the GSC flag set when bought (e.g. `enter_warehouse`). **Pass `connects=["zone_a", "zone_b"]` to auto-wire the door into the zone graph** — adds `zm_zonemgr::add_adjacent_zone(...)` to the GSC's `zone_init` so buying the door activates the gated zone (idempotent, recognizes reversed connects). Without `connects`, you wire the flag in GSC by hand.
- `add_chalk_decal(map_name, material, origin, angles, decalsize, sort_layer="Grunge", sort_enum=14)` — places a `misc_volume_decal` projecting a chalk gun-outline onto a wall. `material` is e.g. `"t7_zm_chalk_buy_kuda"`; orient via `angles` so the decal's forward axis points into the wall.
- `add_barricade(map_name, origin, angles=null, hide_pieces=false)` — wood-board zombie barricade (`_prefabs/zm/zm_core/barricade_reciever_wood`). Zombies tear boards off; players repair them. The classic ZM chokepoint.
- `place_perks_in_zone(map_name, perks, zone_center, zone_size, z=null, margin=80)` — bulk helper: distributes N perks evenly around a zone's interior perimeter, facing inward. Each perk goes through `add_perk` so GSC imports auto-manage. For a square zone with 4 perks, lands exactly at the 4 corners.

### Build

- `gdtdb_update()` — refresh asset DB.
- `compile_map(map_name, only_ents=true)` — `.map` → `.d3dbsp`.
- `link_map(map_name, language="english")` — assets → `.ff` fastfile (auto-emits `<map>.ff` and `en_<map>.ff`).
- `build(map_name, only_ents=true)` — full chain, stops on first failure.

### Catalog

- `list_perks()` — all perk slugs and aliases.
- `list_wall_weapons()` — all wall-buy weapon slugs.
- `list_textures(category=null)` — curated BO3 texture catalog. Categories: `walls` (subcategorized: concrete/brick/wood/plaster/metal/stone), `floors`, `trim`, `special` (caulk/clip/trigger/etc.), `chalk_buy` (wall-buy gun outlines), `decals` (subcategorized: blood/grunge/damage/snow/signage). Without a category, returns the full catalog.

## Catalogs

### Perks

| Slug | Aliases | Effect |
|---|---|---|
| `juggernaut` | `juggernog`, `jugger` | +health |
| `sleight_of_hand` | `speed_cola`, `sleight` | faster reload |
| `quick_revive` | `revive` | self-revive (solo) / faster revive (co-op) |
| `double_tap` | `doubletap` | +rate of fire |
| `deadshot` | `deadshot_daiquiri` | aim assist to head |
| `stamin_up` | `marathon` | +sprint duration |
| `mule_kick` | `additionalprimaryweapon` | 3rd weapon slot |
| `gobblegum` | `bgb` | gobblegum machine |

### Wall-buy weapons

`ar_standard`, `ar_cqb`, `ar_longburst`, `ar_marksman`, `smg_standard`, `smg_fastfire`, `smg_versatile`, `shotgun_pump`, `pistol_burst`, `pistol_fullauto`, `frag_grenade`, `bouncingbetty`, `bowie`.

## Tips for efficient use with Claude

- **Inspect before mutating.** Ask "list all `misc_prefab` entities" or "find anything near `[X,Y,Z]`" before placing — avoids stacking perks on top of each other.
- **Default to `only_ents=true`.** Fast iteration. Only do a full build when you've changed geometry, lighting, or per-map prefabs.
- **Talk in absolute world coords.** "Place juggernog 200 units east of the spawn" means the model needs to know the spawn's coordinates first — it'll either ask or check via `find_by_targetname("initial_spawn_points")`.
- **Group your placements.** "Place all four perks: jugger at A, speed cola at B, double tap at C, mule kick at D" runs faster than four separate turns.
- **Use the GUID for cross-call references.** `list_entities` returns GUIDs; pass them back to `move_entity` / `update_entity_kvps` / `delete_entity`.
- **Layers are cosmetic.** They control Radiant's organization tree, not compile behavior. Adding entities outside known layers is fine — the compile will still pick them up.

## Troubleshooting

### "BO3 mod tools not found at..."
The `paths.MOD_TOOLS_ROOT` constant doesn't match your install. Set the `BO3_MOD_TOOLS` environment variable to the correct path.

### Compile errors "Material 'X' is missing"
A texture referenced by a brush isn't in your asset DB. Run `gdtdb_update`, then re-compile. If it persists, the material genuinely doesn't exist — check for typos or missing GDT files.

### Linker error "Must specify at least one valid language"
Internal sanity check failed — the linker tool is being called without `-language english`. Should never happen via the MCP; if it does, file a bug.

### `^3DROPPED VERTS` warnings during link
Per-asset mesh conversion warnings. Cosmetic — won't fail the build. The full per-asset log is at `share/assetconvert/xmesh/v6/<asset>_<hash>.log`.

### Brushes appear inside-out / the world is a void
If geometry compiles but you fall through floors or the inside of walls is rendered while the outside isn't, the face winding convention is wrong. Treyarch uses **inward-normal** winding (verified empirically against `zm_giant`'s magic-box prefab and confirmed via cod2map64 compile). If a future tools update flips this, edit `_face_points` in `bo3_mcp/brushes.py` to use the opposite winding.

### Layer "DO NOT COMPILE" ignored
Expected — that's a built-in convention. Layers with the `ignore` flag in the `.map` header are skipped during compile. Useful for staging WIP geometry.

### Build seems to hang for many minutes
First-ever link of a map converts every referenced xmesh and image — can take 5–10 minutes. Subsequent builds use the cache and finish in seconds. Check the launcher's Output Window if you want to see progress.

## Architecture (brief)

```
bo3_mcp/
  paths.py         install root + path helpers + build env vars
  mapfile.py       parser/writer for the iwmap 4 text format
  entities.py      generic entity CRUD on a MapFile
  brushes.py       axis-aligned box brush synthesis (face winding, format)
  geometry.py      high-level geometry: carve_room, carve_room_with_openings, add_stairs, doorways
  gsc.py           GSC editing: #using auto-management, init_zones[] append,
                   default_start_location update
  zm.py            ZM recipe helpers (perks, box, pap, wall buys, spawners,
                   zones, doors, barricades, decals, bulk ops) + perk/weapon catalogs
  textures.py      ground-truth texture catalog vetted against zm_giant brush
                   face lines (walls / floors / trim / special / chalk_buy / decals)
  scaffold.py      create_zombie_map + GSC/CSC/.map/.zone templates
  demo.py          make_demo_map — one-call 3-zone playable shell
  pipeline.py      gdtdb / cod2map64 / linker_modtools subprocess wrappers
                   + build output parser (color codes, summaries, per-asset warns,
                   leak detection, missing-material aggregation)
  server.py        FastMCP stdio entry, registers 35 tools
```

The parser keeps brush bodies as opaque text and only structures entities and their KVPs. This means brush authoring stays in Radiant — we don't risk corrupting geometry in round-trip — while still letting us add/move/delete entities freely.

For zombie maps specifically, the architecture leans on a key insight: most ZM placements are `misc_prefab` entities pointing into Treyarch's stock prefab library at `_prefabs/zm/zm_core/`. So `add_perk("juggernaut", ...)` reduces to a one-line entity addition referencing `_prefabs/zm/zm_core/vending_juggernaut_struct.map`.

## Roadmap

### Done in v0.5
- ✅ Axis-aligned box brush synthesis: `add_box_brush`, `add_floor`, `add_wall`, `carve_room`, `add_stairs`, `add_doorway_to_wall`.
- ✅ Brush-volume bounds for `add_zombie_zone` (synthesized caulk box).
- ✅ Verified against real `cod2map64` compile — winding correct, BSP produced cleanly.

### Done in v0.6
- ✅ `carve_room_with_openings` — single-call hollow room with N pre-cut doorways or windows.
- ✅ GSC `#using` auto-management for `add_perk` and `add_pack_a_punch` — uncomments the scaffolded import or inserts a new one; idempotent.

### Done in v0.7
- ✅ Zone GSC integration — `add_zombie_zone` auto-appends to `init_zones[]` (idempotent), updates `default_start_location` if starting zone.
- ✅ `add_zombie_spawner` — `actor_spawner_zm_factory_zombie` with the full standard AI-tuning KVP battery extracted from `zm_giant_nodes.map`.
- ✅ `add_buyable_door` — 2-entity recipe (`script_brushmodel` + `trigger_use`) with the framework's `zombie_door` targetname convention; `script_flag` set on purchase.

### Done in v0.8
- ✅ Door → zone auto-wire: `add_buyable_door(connects=["a","b"])` inserts `zm_zonemgr::add_adjacent_zone(...)` into `zone_init`. Idempotent across reversed connects.
- ✅ Multi-opening per wall in `carve_room_with_openings` — sweep-line decomposition handles any number of doorways/windows on the same side.
- ✅ `add_chalk_decal` — `misc_volume_decal` for wall-buy gun outlines.
- ✅ Per-face `face_textures` override exposed through MCP tools (caulk on hidden faces, mixed materials, etc.).

### Done in v0.9
- ✅ `add_barricade` — wood-board barricade via `zm_core/barricade_reciever_wood`. The zombie chokepoint primitive.
- ✅ `list_textures` — curated catalog of common BO3 textures organized by category.
- ✅ `place_perks_in_zone` — bulk helper: distributes N perks around a zone's interior perimeter, GSC-import-managed.

### Done in v1.0
Driven by issues that surfaced during the first real `zm_chronos_lab` build:
- ✅ **Texture catalog vetted against ground truth**: every entry in `textures.py` was extracted from actual brush face lines in shipping `zm_giant` source. Replaces inferred-from-images guesses (which had the wrong names — `t7_metal_panel_4x4` should have been `t7_metal_panel_4x4_tungsten_polished`, etc.). 150+ "Material is missing" warnings → 1.
- ✅ **Scaffold orphan removed**: scaffolded GSC no longer ships `init_zones[0] = "start_zone";` placeholder. The first `add_zombie_zone` call now lands at `init_zones[0]` cleanly. `set_default_start_location` learned to insert (not just replace) the line.
- ✅ **Leak detection surfaced**: `pipeline.parse_warnings` now extracts the `****** leaked ******` banner and the `.lin` leakfile path; build summaries include a `leak: {leaked, leakfile, fix}` block. Compile silently succeeding through a leak no longer happens.
- ✅ **Tool docstring polish**: `list_textures`, `add_zombie_zone`, `add_perk`, `carve_room_with_openings` rewritten for clearer planning context (the descriptions are what Claude reads when picking helpers).
- ✅ **`make_demo_map`**: one-call helper that scaffolds + builds a 3-zone playable shell. Useful as a starting template and as a regression check.

### Known caveats (v1.0+ tracking)
- **`jun_art_wood_plywood_dark03` warning persists.** The mystery box prefab (`zm_core/buyable_magic_box_start.map`) references this material internally. Cosmetic compile warning only — the prefab works in zm_giant, so the asset exists; custom maps may need the material added to the `.zone` manifest.

### Leak fix (v1.0 follow-up)
The "leaks in multi-room layouts" caveat from earlier was wrong. The actual cause: **the barricade prefab places `exterior_goal` and `zbarrier` entities ~50 units BEYOND the wall** the barricade is mounted against (where zombies spawn from). In real BO3 maps these entities sit between the playable rooms and an outer vista shell. In our synthesized maps the playable rooms are floating in unbounded void — cod2map64's leak detector flood-fills from the barricade's exterior entities to infinity and reports a leak.

Fixed by `seal_exterior(map_name, mins, maxs, buffer=128)`:
- 6 caulk slabs surrounding the playable bounds (with a 128-unit buffer)
- Caulk is non-rendering, so the shell is invisible in-game
- Makes the void around exterior entities *finite*, eliminating the leak

`make_demo_map` now calls `seal_exterior` automatically. For your own maps with barricades, call it once at the end (before compile). Maps without barricades don't need it.

### Next (v1.1)
- `make_demo_map` parameterized (room count, layout style, perk loadout).
- Auto-include common materials (`material,jun_art_wood_plywood_dark03`, etc.) in the scaffolded `.zone` manifest.
- More bulk ops: `tile_floor`, `arrange_along_path`, `add_catwalk_with_access` (catwalk + stairs in one helper).
- `add_buyable_debris` — alternative entry pattern using the `zbarrier_zm_factory_debris` recipe.
- Sound bundle scaffolding (so audio-related linker errors stop being a thing).
- A real first-time link of a synthesized minimal map (closing the last untested loop).
- Auto-call `seal_exterior` from a "build helper" that tracks the playable bounds across all `carve_room*` calls (so users don't have to compute mins/maxs themselves).

### Later
- Light volume synthesis for ambient lighting hints.
- Easter-egg step builder helpers (round-based progressions, multi-step quests).
- Texture catalog tool (`list_textures` with categories like wall/floor/decal/clip).
- Patch / curved geometry primitives (cylinders, arches).

### Won't
- Replicating Radiant's full visual editor in chat. The right tool for visual sculpting is the visual editor.
- Lighting bake / LED export. Stays in Radiant — needs the renderer.

## Acknowledgements

Built against Treyarch's official BO3 Mod Tools (Steam appid 455130). Map format and tool invocations were reverse-engineered from `zm_giant`'s shipping source files and a real launcher build run.
