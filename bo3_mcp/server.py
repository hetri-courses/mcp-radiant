"""MCP server entry point. Exposes the bo3_mcp surface over stdio so Claude
Code (or any MCP client) can drive map authoring and builds."""

from __future__ import annotations

import importlib

from mcp.server.fastmcp import FastMCP

from . import brushes, demo, entities, geometry, mapfile, paths, pipeline, scaffold, textures, zm

mcp = FastMCP("bo3-mcp")


def _xyz(values: list[float]) -> tuple[float, float, float]:
    if len(values) != 3:
        raise ValueError(f"expected 3 values, got {len(values)}")
    return float(values[0]), float(values[1]), float(values[2])


# --- Dev: hot-reload --------------------------------------------------------


@mcp.tool()
def _reload_mcp_modules() -> dict:
    """Re-import all bo3_mcp submodules in dependency order so edits to the
    MCP source code take effect WITHOUT restarting Claude Desktop.

    Use this after editing any file in bo3_mcp/ (zm.py, demo.py, geometry.py
    etc.) — calls to MCP tools on the next request will use the freshly
    imported code. Without this (or a Claude Desktop restart), Python's
    module cache holds the old code and edits silently no-op.

    Reloads in topological order (leaves first) so each module sees fresh
    versions of the modules it imports from. Server.py itself is NOT
    reloaded (would re-register tools, breaking the FastMCP runtime); but
    server.py uses `from . import zm` style — so its `zm.add_perk(...)`
    lookups happen at call time and pick up the reloaded module."""
    # Order: leaves first, then modules that depend on them.
    reload_order = [
        "bo3_mcp.brushes",      # primitives, no internal deps
        "bo3_mcp.paths",        # path resolution, no internal deps
        "bo3_mcp.mapfile",      # parser/serializer, depends on stdlib only
        "bo3_mcp.textures",     # catalog, no internal deps
        "bo3_mcp.entities",     # depends on mapfile
        "bo3_mcp.gsc",          # depends on stdlib only
        "bo3_mcp.geometry",     # depends on brushes, mapfile, paths, entities
        "bo3_mcp.zm",           # depends on brushes, entities, geometry, gsc, mapfile, paths
        "bo3_mcp.scaffold",     # depends on paths, mapfile
        "bo3_mcp.pipeline",     # depends on paths
        "bo3_mcp.demo",         # depends on geometry, scaffold, zm
    ]
    import sys

    reloaded: list[str] = []
    skipped: list[dict] = []
    for module_name in reload_order:
        if module_name not in sys.modules:
            skipped.append({"module": module_name, "reason": "not loaded"})
            continue
        try:
            importlib.reload(sys.modules[module_name])
            reloaded.append(module_name)
        except Exception as e:
            skipped.append({"module": module_name, "reason": f"{type(e).__name__}: {e}"})
    return {
        "reloaded": reloaded,
        "reloaded_count": len(reloaded),
        "skipped": skipped,
        "note": (
            "Subsequent MCP tool calls will use the freshly reloaded code. "
            "If you edited server.py itself (not just submodules), a full "
            "Claude Desktop restart is still required."
        ),
    }


# --- Map inspection --------------------------------------------------------


@mcp.tool()
def list_entities(map_name: str, classname: str | None = None) -> list[dict]:
    """List entities in a map's top-level .map file. Optionally filter by classname.
    Returns compact summaries (guid, classname, origin, layer, targetname, model)."""
    mf = mapfile.load(paths.map_source(map_name))
    rows = entities.list_entities(mf, classname=classname)
    return entities.summarize_many(rows)


@mcp.tool()
def find_entities_near(
    map_name: str,
    origin: list[float],
    max_distance: float,
    classname: str | None = None,
) -> list[dict]:
    """Find entities within `max_distance` of `origin`. Sorted by distance ascending."""
    mf = mapfile.load(paths.map_source(map_name))
    results = entities.find_near(mf, _xyz(origin), max_distance, classname=classname)
    return [{"distance": d, **entities.summarize(e)} for d, e in results]


@mcp.tool()
def find_by_targetname(map_name: str, targetname: str) -> list[dict]:
    """Find entities whose `targetname` KVP matches exactly."""
    mf = mapfile.load(paths.map_source(map_name))
    return [entities.summarize(e) for e in entities.find_by_targetname(mf, targetname)]


@mcp.tool()
def get_entity(map_name: str, guid: str) -> dict | None:
    """Fetch a single entity by GUID. Returns the full KVP dict, layer, and origin.
    Use list_entities to find GUIDs."""
    mf = mapfile.load(paths.map_source(map_name))
    entity = entities.find_by_guid(mf, guid)
    if entity is None:
        return None
    return {
        "guid": entity.guid,
        "layer": entity.layer,
        "kvps": dict(entity.kvps),
        "origin": entity.origin,
        "brush_count": len(entity.brushes),
    }


# --- Map editing -----------------------------------------------------------


@mcp.tool()
def update_entity_kvps(map_name: str, guid: str, kvps: dict[str, str]) -> dict:
    """Patch KVPs on an entity (top-level map). Pass `null` value to delete a key.
    Returns the entity summary after the patch."""
    target = paths.map_source(map_name)
    mf = mapfile.load(target)
    entity = entities.find_by_guid(mf, guid)
    if entity is None:
        raise ValueError(f"no entity with guid {guid}")
    entities.update_entity(entity, kvps)
    mf.save(target)
    return entities.summarize(entity)


@mcp.tool()
def move_entity(map_name: str, guid: str, origin: list[float]) -> dict:
    """Set an entity's origin. Affects only the top-level .map file."""
    target = paths.map_source(map_name)
    mf = mapfile.load(target)
    entity = entities.find_by_guid(mf, guid)
    if entity is None:
        raise ValueError(f"no entity with guid {guid}")
    entities.move_entity(entity, _xyz(origin))
    mf.save(target)
    return entities.summarize(entity)


@mcp.tool()
def delete_entity(map_name: str, guid: str) -> dict:
    """Delete an entity from the top-level .map file by GUID."""
    target = paths.map_source(map_name)
    mf = mapfile.load(target)
    if not entities.delete_entity(mf, guid):
        raise ValueError(f"no entity with guid {guid}")
    mf.save(target)
    return {"deleted": guid, "target_file": str(target)}


# --- Scaffolding -----------------------------------------------------------


@mcp.tool()
def make_demo_map(name: str, overwrite: bool = False) -> dict:
    """Build a complete 3-zone playable shell in one call. Composes scaffold +
    geometry + zone/door/spawn/perk/box/pap helpers — same recipe a user
    would use, just packaged.

    Layout:
      - start_zone (small starter, west) — player spawn, starter pistol
      - arena_zone (large central) — 4 perks at corners, AR + SMG wall buys, barricades
      - vault_zone (east) — mystery box, pack-a-punch, power switch

    Two doors connect the zones (start→arena 500pts, arena→vault 1500pts),
    auto-wired via add_adjacent_zone in the GSC. Six zombie spawners
    distributed across all three zones. Use this when you want to see a
    working baseline before customizing your own layout."""
    return demo.make_demo_map(name, overwrite=overwrite)


@mcp.tool()
def scaffold_zombie_map(name: str, overwrite: bool = False) -> dict:
    """Create a new custom zombie map's full directory tree and template files.
    The `name` must start with `zm_` (e.g. `zm_parlor`).

    Creates: top-level .map, per-concern prefabs (vending/magicboxes/weapons),
    GSC + CSC scripts, zone manifest. After this, open the .map in Radiant
    to sculpt geometry; use the add_* tools for everything else."""
    return scaffold.create_zombie_map(name, overwrite=overwrite)


# --- ZM placement helpers --------------------------------------------------


@mcp.tool()
def add_perk(
    map_name: str,
    perk: str,
    origin: list[float],
    angles: list[float] | None = None,
) -> dict:
    """Place a perk machine via misc_prefab into the map's vending prefab,
    AND auto-uncomment the matching `#using scripts\\zm\\_zm_perk_<x>;`
    line in the GSC (so the framework knows to enable that perk).

    Perks (all aliases accepted):
      - `juggernaut` (a.k.a. juggernog) — +health
      - `sleight_of_hand` (a.k.a. speed_cola) — faster reload
      - `quick_revive` (a.k.a. revive) — self-revive solo / faster revive co-op
      - `double_tap` (a.k.a. doubletap) — +rate of fire
      - `deadshot` (a.k.a. deadshot_daiquiri) — aim assist to head
      - `stamin_up` (a.k.a. marathon) — +sprint duration
      - `mule_kick` (a.k.a. additionalprimaryweapon) — 3rd weapon slot
      - `gobblegum` (a.k.a. bgb)

    For bulk placement of all perks in a zone, use `place_perks_in_zone`."""
    return zm.add_perk(
        map_name, perk, _xyz(origin), _xyz(angles) if angles else (0.0, 0.0, 0.0)
    )


@mcp.tool()
def add_pack_a_punch(
    map_name: str,
    origin: list[float],
    angles: list[float] | None = None,
) -> dict:
    """Place pack-a-punch in the vending prefab."""
    return zm.add_pack_a_punch(
        map_name, _xyz(origin), _xyz(angles) if angles else (0.0, 0.0, 0.0)
    )


@mcp.tool()
def add_mystery_box(
    map_name: str,
    origin: list[float],
    angles: list[float] | None = None,
) -> dict:
    """Place a mystery box spawn. The first one added is the starting box;
    subsequent ones are random teleport destinations."""
    return zm.add_mystery_box(
        map_name, _xyz(origin), _xyz(angles) if angles else (0.0, 0.0, 0.0)
    )


@mcp.tool()
def add_power_switch(
    map_name: str,
    origin: list[float],
    angles: list[float] | None = None,
) -> dict:
    """Place the power switch in the top-level map. The prefab includes a
    built-in trigger_use that the GSC framework recognizes."""
    return zm.add_power_switch(
        map_name, _xyz(origin), _xyz(angles) if angles else (0.0, 0.0, 0.0)
    )


@mcp.tool()
def add_wall_buy(
    map_name: str,
    weapon: str,
    origin: list[float],
    angles: list[float] | None = None,
) -> dict:
    """Place a wall-buy weapon. Available: ar_standard, ar_cqb, ar_longburst,
    ar_marksman, smg_standard, smg_fastfire, smg_versatile, shotgun_pump,
    pistol_burst, pistol_fullauto, frag_grenade, bouncingbetty, bowie."""
    return zm.add_wall_buy(
        map_name, weapon, _xyz(origin), _xyz(angles) if angles else (0.0, 0.0, 0.0)
    )


@mcp.tool()
def add_player_spawn(
    map_name: str,
    origin: list[float],
    angles: list[float] | None = None,
    player_slot: int = 1,
) -> dict:
    """Add an `initial_spawn_points` script_struct so the GSC framework spawns
    a player here. Use player_slot 1-4 for co-op."""
    return zm.add_player_spawn(
        map_name,
        _xyz(origin),
        _xyz(angles) if angles else (0.0, 0.0, 0.0),
        player_slot=player_slot,
    )


@mcp.tool()
def add_barricade(
    map_name: str,
    origin: list[float],
    angles: list[float] | None = None,
    hide_pieces: bool = False,
) -> dict:
    """Place a wood-board zombie barricade — the classic chokepoint with
    breakable boards that zombies tear off and players repair. Drops a
    `_prefabs/zm/zm_core/barricade_reciever_wood` reference. Set
    `hide_pieces=true` for the variant where torn boards despawn.

    For the FULL barricade-window pattern (barricade + matching outside-wall
    riser spawn struct linked by script_string), use `add_zombie_window`
    instead — it does both placements in one call so zombies actually rise
    OUTSIDE the wall and walk through the boards."""
    return zm.add_barricade(
        map_name,
        _xyz(origin),
        _xyz(angles) if angles else (0.0, 0.0, 0.0),
        hide_pieces=hide_pieces,
    )


@mcp.tool()
def add_zombie_window(
    map_name: str,
    origin: list[float],
    yaw: float,
    zone_name: str,
    spawn_offset: float = 96.0,
    script_string: str | None = None,
    hide_pieces: bool = False,
) -> dict:
    """Complete window-spawn setup in one call: barricade prefab at the wall +
    matching exterior riser spawn struct ~96 units further out, both linked
    by `script_string`. Replicates zm_template_test's "receiver_set_entry_a"
    pattern.

    Use this instead of `add_zombie_spawner` when you want zombies to rise
    OUTSIDE the wall and walk through the boards (the canonical zombie-map
    feel) — vs. spawning them inside the room where they often glitch on
    the spawner cube without proper navmesh access.

    Args:
        origin: barricade prefab origin — at the wall opening, z=floor surface
            (z=16 for our scaffolds).
        yaw: 0 (north wall, ext faces +Y) / 90 (east, +X) / 180 (south, -Y) /
            270 (west, -X). Must be axis-aligned.
        zone_name: spawn struct gets `targetname "<zone>_spawners"`.
        spawn_offset: distance OUTSIDE the wall for the riser. Default 96.
        script_string: shared identifier; auto-generated if omitted.
        hide_pieces: use the no-debris barricade variant.

    Note: barricade-style spawners require `seal_exterior(...)` (or
    `add_lighting_kit`) to wrap the playable area. Without it, the spawn
    struct sits in unbounded void and cod2map64 reports a leak."""
    return zm.add_zombie_window(
        map_name,
        _xyz(origin),
        yaw,
        zone_name=zone_name,
        spawn_offset=spawn_offset,
        script_string=script_string,
        hide_pieces=hide_pieces,
    )


@mcp.tool()
def place_perks_in_zone(
    map_name: str,
    perks: list[str],
    zone_center: list[float],
    zone_size: list[float],
    z: float | None = None,
    margin: float = 80.0,
) -> dict:
    """Bulk helper: distribute N perks around a zone's interior perimeter,
    facing inward. Goes through `add_perk` for each, so GSC imports are
    auto-managed. `zone_size` and `zone_center` should match what you passed
    to `add_zombie_zone` (or any rectangular bounds you want to lay perks
    against)."""
    return zm.place_perks_in_zone(
        map_name, perks,
        _xyz(zone_center),
        _xyz(zone_size),
        z=z, margin=margin,
    )


@mcp.tool()
def add_zombie_spawner(
    map_name: str,
    origin: list[float],
    angles: list[float] | None = None,
    zone_name: str | None = None,
    count: int = 9999,
    location_type: str = "spawn_location",
) -> dict:
    """Place a zombie spawner — emits an `actor_spawner_zm_factory_zombie`
    factory plus a sibling `script_struct` spawn-position tagged for the zone.

    **Pass `zone_name`** (e.g. `"cryo_zone"` or `"cryo"`) to wire the spawner
    to a zone — without it, no zombies will appear in that zone since the
    framework discovers spawn points by `targetname "<zone>_spawners"`, not by
    spatial overlap. Match the zone you registered via `add_zombie_zone`.

    `location_type`: `"spawn_location"` (basic walking, default),
    `"riser_location"` (rises from ground), `"faller_location"` (falls from
    above), `"custom_spawner_entry"` (script-driven).

    `count=9999` is effectively unlimited; set lower for finite waves."""
    return zm.add_zombie_spawner(
        map_name,
        _xyz(origin),
        _xyz(angles) if angles else (0.0, 0.0, 0.0),
        zone_name=zone_name,
        count=count,
        location_type=location_type,
    )


@mcp.tool()
def add_buyable_door(
    map_name: str,
    door_mins: list[float],
    door_maxs: list[float],
    cost: int,
    script_flag: str,
    connects: list[str] | None = None,
    door_name: str | None = None,
    slide_vector: list[float] | None = None,
    door_texture: str = "clip",
    door_model: str | None = "p7_zm_der_door_buy_std_onepiece",
    door_model_yaw: float = 0.0,
    door_model_z_offset: float = 0.0,
    trigger_inflate: float = 128.0,
) -> dict:
    """Create a buyable door — Treyarch's 3-entity pattern from
    `_prefabs/zm/zm_giant/geo/factory_doors.map`:

    1. `script_brushmodel` with `clip` texture (collision; invisible)
    2. `script_model` with a door asset (visible appearance) — same
       targetname / DYNAMICPATH / script_string / script_vector as the
       brushmodel so they slide together when bought
    3. `trigger_use` (the buy interaction)

    `script_flag` is the GSC flag set when bought (e.g. "enter_warehouse").

    `door_texture` is the brushmodel face texture. Default `"clip"` makes
    the brushmodel invisible-but-solid (Treyarch's pattern); the visible
    appearance comes from `door_model`. Passing a visible texture like
    `"t7_wood_planks_rustic"` is likely to be culled in-game (use a model
    instead).

    `door_model` is the visible script_model asset. Default is Treyarch's
    standard one-piece buyable door. Pass `None` to skip the visible model
    (collision-only invisible door — useful for invisible barriers).
    `door_model_yaw` rotates around Z; `door_model_z_offset` raises/lowers
    the model relative to the door brush bottom.

    Pass `connects=["zone_a", "zone_b"]` to auto-wire the door into the zone
    graph — adds `zm_zonemgr::add_adjacent_zone(...)` to the GSC's zone_init
    function so buying the door activates the gated zone. Without `connects`,
    you get door entities but flag handling is up to you in the GSC."""
    if connects is not None and len(connects) != 2:
        raise ValueError(f"connects must be a 2-element list; got {connects}")
    connects_tuple = tuple(connects) if connects else None
    return zm.add_buyable_door(
        map_name,
        _xyz(door_mins),
        _xyz(door_maxs),
        cost,
        script_flag,
        connects=connects_tuple,  # type: ignore[arg-type]
        door_name=door_name,
        slide_vector=_xyz(slide_vector) if slide_vector else None,
        door_texture=door_texture,
        door_model=door_model,
        door_model_yaw=door_model_yaw,
        door_model_z_offset=door_model_z_offset,
        trigger_inflate=trigger_inflate,
    )


@mcp.tool()
def add_chalk_decal(
    map_name: str,
    material: str,
    origin: list[float],
    angles: list[float] | None = None,
    decalsize: list[float] | None = None,
    sort_layer: str = "Grunge",
    sort_enum: int = 14,
) -> dict:
    """Place a chalk gun-outline decal next to a wall buy.

    `material` is e.g. "t7_zm_chalk_buy_kuda" — see the `i_t7_zm_chalk_buy_*`
    images in the asset DB for available weapons. `decalsize` is
    [depth_into_wall, width, height], default [4, 64, 32]. Orient via
    `angles` so the decal's forward axis points INTO the wall."""
    size = (4.0, 64.0, 32.0) if decalsize is None else (
        float(decalsize[0]), float(decalsize[1]), float(decalsize[2])
    )
    return zm.add_chalk_decal(
        map_name, material,
        _xyz(origin),
        _xyz(angles) if angles else (0.0, 0.0, 0.0),
        decalsize=size,
        sort_layer=sort_layer,
        sort_enum=sort_enum,
    )


@mcp.tool()
def add_zombie_zone(
    map_name: str,
    zone_name: str,
    volume_center: list[float],
    volume_size: list[float],
    is_starting_zone: bool = False,
) -> dict:
    """Create a complete zombie zone in one call. Does FOUR things:

    1. Adds an `info_volume` entity tagged with `script_noteworthy
       "<zone_name>_zone"` — what the GSC zone manager reads to identify zones.
    2. Synthesizes a `caulk` (invisible) brush volume covering the bounds,
       so the info_volume actually has bounds.
    3. Auto-appends the zone to `init_zones[]` in the GSC's main() function
       (idempotent — same zone twice is a no-op).
    4. If `is_starting_zone=true`, sets `level.default_start_location`.

    `volume_center` is [cx, cy, cz] (the geometric middle of the zone);
    `volume_size` is the full [width, depth, height] extents. Pass the
    zone's bare interior dimensions — the volume should cover the playable
    space inside the room. Spawners attach to whichever zone they're
    spatially inside, so size the volume to capture them.

    The `_zone` suffix is added automatically if you forget it (so passing
    "warehouse" and "warehouse_zone" both produce zone name "warehouse_zone")."""
    return zm.add_zombie_zone(
        map_name,
        zone_name,
        _xyz(volume_center),
        _xyz(volume_size),
        is_starting_zone=is_starting_zone,
    )


# --- Catalog (helps the model know what's available) ----------------------


@mcp.tool()
def list_textures(category: str | None = None) -> dict | list:
    """Return vetted BO3 material/texture names for use in `carve_room`,
    `add_box_brush`, `add_chalk_decal`, etc. Every entry was extracted from
    actual brush face lines in shipping `zm_giant` map source — meaning
    each name compiles cleanly without "Material is missing" warnings.

    Categories:
      - `walls` — concrete / brick / wood / plaster / metal / glass / tile / snow (subcategorized)
      - `floors` — flat list of materials suited for floor surfaces
      - `trim` — accent strips and trim materials
      - `special` — non-rendering (caulk), collision-only (clip variants),
                    triggers, and engine-special volume markers (sky, fog,
                    umbra, traverse, etc.)
      - `chalk_buy` — wall-buy gun outline materials (pass to add_chalk_decal)
      - `decals` — blood / grunge / damage / snow (subcategorized; for
                    misc_volume_decal modeloverridematerial)

    Note: many BO3 materials have `_wet` (rain-aware) and `_nw` (no-weather)
    variants. Both are listed where they exist in zm_giant. Use the variant
    that matches your map's weather setup (or `_wet` for snowy zm_giant-style
    maps, base name for clean rooms)."""
    if category is None:
        return textures.list_all()
    return textures.list_category(category)


@mcp.tool()
def list_perks() -> dict:
    """Return the catalog of perks the MCP knows how to place."""
    return {"perks": sorted(set(zm.PERKS.values())), "aliases": dict(zm.PERKS)}


@mcp.tool()
def list_wall_weapons() -> dict:
    """Return the catalog of wall-buy weapons the MCP knows how to place."""
    return {"weapons": dict(zm.WALL_WEAPONS)}


# --- Geometry (v0.5) -------------------------------------------------------


@mcp.tool()
def add_box_brush(
    map_name: str,
    mins: list[float],
    maxs: list[float],
    texture: str = brushes.DEFAULT_TEXTURE,
    face_textures: dict[str, str] | None = None,
) -> dict:
    """Add a single solid axis-aligned box brush to the worldspawn entity.
    Useful as a primitive for ramps, debris, pillars, or anything ad-hoc.
    `mins` and `maxs` are [x, y, z] world coordinates.

    Pass `face_textures={"top": "caulk", ...}` to override individual faces
    (sides: bottom, top, south, north, east, west). Common pattern: caulk on
    hidden faces for cleaner BSP — e.g. on a floor slab put `caulk` on
    bottom/sides, leaving top with the visible texture."""
    return geometry.add_box_brush(
        map_name, _xyz(mins), _xyz(maxs), texture,
        face_textures=face_textures,  # type: ignore[arg-type]
    )


@mcp.tool()
def add_floor(
    map_name: str,
    mins: list[float],
    maxs_xy: list[float],
    thickness: float = 16.0,
    texture: str = brushes.DEFAULT_TEXTURE,
) -> dict:
    """Add a floor slab. `mins` is the bottom-front-left corner; `maxs_xy` is
    [max_x, max_y]; the slab extends upward by `thickness`."""
    if len(maxs_xy) != 2:
        raise ValueError("maxs_xy must be [max_x, max_y]")
    return geometry.add_floor(
        map_name, _xyz(mins), (float(maxs_xy[0]), float(maxs_xy[1])),
        thickness=thickness, texture=texture,
    )


@mcp.tool()
def add_wall(
    map_name: str,
    mins: list[float],
    maxs: list[float],
    texture: str = brushes.DEFAULT_TEXTURE,
) -> dict:
    """Add a wall (semantic alias for add_box_brush). Wall thickness is
    typically 16 units; height is whatever you make the box."""
    return geometry.add_wall(map_name, _xyz(mins), _xyz(maxs), texture)


@mcp.tool()
def carve_room(
    map_name: str,
    mins: list[float],
    maxs: list[float],
    wall_thickness: float = 16.0,
    texture: str = brushes.DEFAULT_TEXTURE,
    floor_texture: str | None = None,
    ceiling_texture: str | None = None,
) -> dict:
    """Build a hollow rectangular room from 6 wall brushes (floor, ceiling,
    four walls) surrounding a void. Returns the void bounds — that's where
    you can safely place perks, spawn points, mystery box etc.

    Note: rooms have NO doorways yet — use `add_doorway_to_wall` to punch a
    hole through one of the walls afterward, or build the walls individually
    with `add_wall` and leave gaps for openings."""
    return geometry.carve_room(
        map_name, _xyz(mins), _xyz(maxs),
        wall_thickness=wall_thickness, texture=texture,
        floor_texture=floor_texture, ceiling_texture=ceiling_texture,
    )


@mcp.tool()
def carve_room_with_openings(
    map_name: str,
    mins: list[float],
    maxs: list[float],
    openings: list[dict],
    wall_thickness: float = 16.0,
    texture: str = brushes.DEFAULT_TEXTURE,
    floor_texture: str | None = None,
    ceiling_texture: str | None = None,
) -> dict:
    """Hollow rectangular room with pre-cut openings (doorways or windows).
    Each opening dict: `{"side": "south"|"north"|"east"|"west", "width": N,
    "height": N, "center_offset": N (default 0), "bottom": N (default 0 =
    on the floor; >0 makes a window)}`. Multiple openings per side are
    supported (v0.8+) — sweep-line decomposition handles any combination."""
    return geometry.carve_room_with_openings(
        map_name, _xyz(mins), _xyz(maxs), openings,
        wall_thickness=wall_thickness, texture=texture,
        floor_texture=floor_texture, ceiling_texture=ceiling_texture,
    )


@mcp.tool()
def add_stairs(
    map_name: str,
    base_origin: list[float],
    step_count: int,
    step_depth: float = 16.0,
    step_height: float = 8.0,
    step_width: float = 96.0,
    direction: str = "+y",
    texture: str = brushes.DEFAULT_TEXTURE,
) -> dict:
    """Generate a flight of stairs as stacked box brushes. `direction` is one
    of "+x", "-x", "+y", "-y". Each step rises step_height and extends
    step_depth past the previous step's edge."""
    return geometry.add_stairs(
        map_name, _xyz(base_origin),
        step_count=step_count,
        step_depth=step_depth, step_height=step_height, step_width=step_width,
        direction=direction,  # type: ignore[arg-type]
        texture=texture,
    )


@mcp.tool()
def add_outdoor_courtyard(
    map_name: str,
    mins: list[float],
    maxs: list[float],
    open_side: str,
    wall_thickness: float = 16.0,
    floor_texture: str = "t7_concrete_trowelled",
    wall_texture: str = "t7_concrete_trowelled",
    ceiling_texture: str = "t7_concrete_trowelled",
) -> dict:
    """Build a 5-walled exterior pocket adjacent to your playable area —
    a small outdoor courtyard with floor, ceiling, and 3 perimeter walls
    (the `open_side` is left out so the playable room's wall serves as
    the courtyard's interior wall).

    Use this when you've placed a barricade window in a room and need
    walkable ground OUTSIDE that wall — without it, zombies spawn in the
    void, take one step, and fall through (no navmesh). The courtyard
    interior is also visible to players peeking through the barricade
    boards, so it cleans up the "raw void/sky" look.

    `open_side`: which side adjoins the playable area — "south", "north",
    "east", or "west". E.g. if you're extending a courtyard SOUTH of the
    playable area, the courtyard's NORTH side is shared with the playable
    room's south wall — pass `open_side="north"`.

    Typical sizing: courtyard depth (perpendicular to open_side) ≥ 144
    so the spawn riser (at ~96 units out) fits with margin."""
    return geometry.add_outdoor_courtyard(
        map_name, _xyz(mins), _xyz(maxs),
        open_side=open_side,  # type: ignore[arg-type]
        wall_thickness=wall_thickness,
        floor_texture=floor_texture,
        wall_texture=wall_texture,
        ceiling_texture=ceiling_texture,
    )


@mcp.tool()
def seal_exterior(
    map_name: str,
    mins: list[float],
    maxs: list[float],
    buffer: float = 128.0,
    shell_thickness: float = 32.0,
    texture: str = "caulk",
) -> dict:
    """Wrap the playable area in a 6-slab shell to seal BSP leaks caused by
    barricade prefabs (their `exterior_goal` and zbarrier entities sit ~50
    units beyond the wall they're mounted against, in unbounded void —
    cod2map64 flood-fills from those entities to infinity and reports a
    leak). The shell makes that void finite.

    `mins`/`maxs` = bounds of your playable area (outer extents of all
    rooms). `buffer` = empty space between rooms and shell (>= 64 to clear
    barricade exterior entities; default 128). Default texture `caulk` is
    non-rendering.

    Call this AFTER all rooms and barricades are placed, BEFORE compile.
    Without it, any map with `add_barricade` will leak. Maps without
    barricades don't need it."""
    return geometry.seal_exterior(
        map_name, _xyz(mins), _xyz(maxs),
        buffer=buffer, shell_thickness=shell_thickness, texture=texture,
    )


@mcp.tool()
def add_sun_volume(
    map_name: str,
    mins: list[float],
    maxs: list[float],
    sun_set: str = "default_day",
    shadow_split_distance: int = 2000,
    grid_density: int = 32,
) -> dict:
    """Set up baked sun lighting — a `volume_sun` brush entity covering
    `mins`→`maxs`. REQUIRED for the lighting bake step.

    Matches Treyarch's official ZM template (`rex/templates/ZM Mod Level/`).
    No info_null target — the `ssi`/`ssi1` GDT preset provides sun direction.

    `sun_set = "default_day"` matches the worldspawn `ssi`/`wsi` KVPs that
    `scaffold_zombie_map` writes. Use other GDT presets like `default_night`
    or `default_storm` for different moods."""
    return zm.add_sun_volume(
        map_name,
        _xyz(mins),
        _xyz(maxs),
        sun_set=sun_set,
        shadow_split_distance=shadow_split_distance,
        grid_density=grid_density,
    )


@mcp.tool()
def add_umbra_volume(
    map_name: str,
    mins: list[float],
    maxs: list[float],
) -> dict:
    """Add an `umbra_volume` brush entity wrapping the playable area.
    REQUIRED for BO3's BSP compiler to properly compute visibility.

    Without it, the compile says "UMBRA volume NOT set, defaulting to
    million-unit cube" and the resulting BSP renders incorrectly in-game
    (player sees skybox where walls should be — the void bug).

    Pair with a sky-textured shell (`seal_exterior`) so the umbra+skybox
    combo triggers cod2map64's "restricting BSP to sky brushes" pass.
    Without the umbra_volume, you don't get that pass and rendering breaks."""
    return zm.add_umbra_volume(map_name, _xyz(mins), _xyz(maxs))


@mcp.tool()
def add_reflection_probe(
    map_name: str,
    origin: list[float],
    radius: float = 2048.0,
) -> dict:
    """Add a `reflection_probe` entity — captures local environment reflections
    for materials that use environment mapping (metals, glass, wet surfaces).

    Treyarch's ZM template ships with one centered probe; small maps work
    fine with one at the playable area's center. Larger maps benefit from
    multiple probes spread through different rooms."""
    return zm.add_reflection_probe(map_name, _xyz(origin), radius=radius)


@mcp.tool()
def add_volume_fpstool(
    map_name: str,
    mins: list[float],
    maxs: list[float],
) -> dict:
    """Add a `volume_fpstool` brush entity wrapping the playable area.
    Used by the lighting bake's FPS profiling pass; Treyarch's template
    includes one. Not strictly required but improves bake quality reporting."""
    return zm.add_volume_fpstool(map_name, _xyz(mins), _xyz(maxs))


@mcp.tool()
def add_lighting_kit(
    map_name: str,
    playable_mins: list[float],
    playable_maxs: list[float],
    buffer: float = 128.0,
    shell_thickness: float = 32.0,
    sun_set: str = "default_day",
    with_reflection_probe: bool = False,
    reflection_probe_origin: list[float] | None = None,
) -> dict:
    """**RECIPE — collapses 4-5 separate tool calls into one.** Sets up
    everything BO3 needs for a sealed map to render correctly:

      1. seal_exterior with sky texture (world boundary)
      2. add_sun_volume (sun lighting reference)
      3. add_umbra_volume (visibility solver scope — the critical missing
         piece that causes the void rendering bug if omitted)
      4. add_volume_fpstool (bake quality reporting)
      5. add_reflection_probe (OPTIONAL — disabled by default)

    All wrap the same `playable_mins → playable_maxs` area with appropriate
    margins. Reflection probe is OFF by default because the lighting bake
    (`radiant_modtools.exe`) errors out (`Probe in level has no counterpart
    in LED!`) when probes are placed at positions where the bake doesn't
    sample. Set `with_reflection_probe=True` only after verifying the bake
    handles it (place probes manually per-room is more reliable for multi-
    room maps).

    Call this AFTER all rooms are placed, BEFORE compile. Replaces the manual
    sequence of 4-5 tool calls with one."""
    probe = _xyz(reflection_probe_origin) if reflection_probe_origin else None
    return zm.add_lighting_kit(
        map_name,
        _xyz(playable_mins),
        _xyz(playable_maxs),
        buffer=buffer,
        shell_thickness=shell_thickness,
        sun_set=sun_set,
        with_reflection_probe=with_reflection_probe,
        reflection_probe_origin=probe,
    )


@mcp.tool()
def furnish_zone(
    map_name: str,
    zone_name: str,
    perks: list[str] | None = None,
    perk_zone_center: list[float] | None = None,
    perk_zone_size: list[float] | None = None,
    perk_margin: float = 80.0,
    wall_buys: list[dict] | None = None,
    spawner_origins: list[list[float]] | None = None,
    light_origins: list[list[float]] | None = None,
    light_color: list[float] | None = None,
    light_radius: float = 320.0,
    light_stops: float = 4.0,
) -> dict:
    """**RECIPE** — bulk-furnish a zone with perks + wall buys + spawners +
    lights in one call. Each is optional; omit to skip.

    Args (all optional):
      `perks` — perk slugs (e.g. `["juggernaut", "speed_cola"]`). If provided,
        you must also pass `perk_zone_center` and `perk_zone_size` for
        perimeter placement.
      `wall_buys` — list of `{"weapon": "smg_standard", "origin": [x,y,z],
        "angles": [0, 90, 0]}` dicts.
      `spawner_origins` — list of `[x, y, z]` for zombie spawners; each gets
        zone-linked to `zone_name` automatically.
      `light_origins` — list of `[x, y, z]` for ceiling lights. Same color/
        radius/stops applied to all (for varied lights, use `add_light` directly).

    Replaces what previously took 4-12 separate tool calls per zone. Use this
    after `add_zombie_zone` and `carve_room_*` for a quick zone setup."""
    return zm.furnish_zone(
        map_name, zone_name,
        perks=perks,
        perk_zone_center=_xyz(perk_zone_center) if perk_zone_center else None,
        perk_zone_size=_xyz(perk_zone_size) if perk_zone_size else None,
        perk_margin=perk_margin,
        wall_buys=wall_buys,
        spawner_origins=[tuple(o) for o in spawner_origins] if spawner_origins else None,
        light_origins=[tuple(o) for o in light_origins] if light_origins else None,
        light_color=tuple(light_color) if light_color else (1.0, 0.95, 0.85),  # type: ignore[arg-type]
        light_radius=light_radius,
        light_stops=light_stops,
    )


@mcp.tool()
def add_light(
    map_name: str,
    origin: list[float],
    color: list[float] | None = None,
    radius: float = 256.0,
    stops: float = 4.0,
    light_def: str = "white_light",
    primary_type: str = "PRIMARY_OMNI",
    probe_only: bool = False,
    bulb_radius: float = 4.0,
    falloff_distance: float = 8.0,
) -> dict:
    """Place an interior `light` entity. **Required for sealed indoor rooms** —
    `add_sun_volume`'s bake can't illuminate fully-enclosed boxes (no opening
    for sun rays), so without lights those rooms are nearly black except for
    muzzle flashes.

    Defaults: warm white (1, 0.95, 0.85), 256-unit radius, 4 stops (medium-
    bright omni). Tune per room:
      - Small room (~512x512): radius 192-256, stops 3-4, place 1 in center
      - Large hall (~1024x1024): radius 384-512, stops 5-6, place 2-3
      - Corridor: radius 200, stops 3, every ~256 units along length

    `color`: RGB 0..1. Examples: warm white (1, 0.95, 0.85), cold blue
    (0.5, 0.7, 1), red alarm (1, 0.2, 0.2), green sci-fi (0.6, 1, 0.7).

    `stops`: photographic stops — each +1 doubles intensity. 0 = barely lit,
    4 = standard interior, 7+ = harsh.

    `primary_type`: PRIMARY_OMNI (point), PRIMARY_SPOT (cone), PRIMARY_TYPE_NONE.
    `probe_only=False` (default) bakes light into lightmaps. Set True only
    for lights that exist purely for reflection probes (zm_giant pattern, but
    that map has openings letting sun in)."""
    if color is not None and len(color) != 3:
        raise ValueError(f"color must be a 3-element list (rgb 0..1); got {color}")
    color_t = tuple(color) if color else (1.0, 0.95, 0.85)
    return zm.add_light(
        map_name,
        _xyz(origin),
        color=color_t,  # type: ignore[arg-type]
        radius=radius,
        stops=stops,
        light_def=light_def,
        primary_type=primary_type,
        probe_only=probe_only,
        bulb_radius=bulb_radius,
        falloff_distance=falloff_distance,
    )


@mcp.tool()
def add_doorway_to_wall(
    map_name: str,
    wall_mins: list[float],
    wall_maxs: list[float],
    opening_mins: list[float],
    opening_maxs: list[float],
    texture: str = brushes.DEFAULT_TEXTURE,
) -> dict:
    """Build a wall with a rectangular hole. Replaces what a single wall would
    have produced with up to 4 sub-walls (above, below, left, right of the
    opening). `wall_mins`/`wall_maxs` are the FULL wall bounds as if no hole;
    the opening rectangle must be entirely within them."""
    return geometry.add_doorway_to_wall(
        map_name, _xyz(wall_mins), _xyz(wall_maxs),
        _xyz(opening_mins), _xyz(opening_maxs),
        texture=texture,
    )


# --- Build chain -----------------------------------------------------------


@mcp.tool()
def gdtdb_update() -> dict:
    """Refresh the GDT asset database. Always run before compile/link."""
    return pipeline.gdtdb_update()


@mcp.tool()
def compile_map(map_name: str, only_ents: bool = True) -> dict:
    """Compile a .map source into a .d3dbsp. `only_ents=true` (default) skips
    geometry/lighting for fast iteration on entity changes (~seconds). Set
    false for full compile when you need geometry/prefab edits to take effect."""
    return pipeline.compile_map(map_name, only_ents=only_ents)


@mcp.tool()
def bake_lighting(
    map_name: str,
    quality: str = "medium",
    timeout: int = 600,
) -> dict:
    """Bake high-quality lightmaps via radiant_modtools — the "Light"
    checkbox in the Mod Tools Launcher. Should run AFTER compile and
    BEFORE link. Skipping this leaves the map with cod2map64's basic
    light grid (functional but flat-looking, no light bounces).

    `quality`: "draft" / "medium" (default) / "final". Takes 30s-2min for
    small maps. EXPERIMENTAL — exact CLI form is partially guessed."""
    return pipeline.bake_lighting(map_name, quality=quality, timeout=timeout)


@mcp.tool()
def link_map(map_name: str, language: str = "english") -> dict:
    """Run linker_modtools — packs the BSP + assets from the zone manifest into
    .ff fastfiles. First-time link can take minutes (asset conversion)."""
    return pipeline.link(map_name, language=language)


@mcp.tool()
def build(map_name: str, only_ents: bool = True) -> dict:
    """Full chain: gdtdb update -> compile -> link. Stops on first failure.
    Does NOT include lighting bake — use `build_full` for that."""
    return pipeline.build(map_name, only_ents=only_ents)


@mcp.tool()
def build_full(
    map_name: str,
    quality: str = "medium",
    skip_gdtdb: bool = True,
) -> dict:
    """One-call replacement for the launcher's Build button: compile (full
    geometry) → bake_lighting → link. Optionally runs gdtdb_update first
    (skipped by default since it's slow and only needed when new assets
    were added).

    Replaces the workflow of: `compile_map` (MCP) → switch to launcher →
    tick Compile/Light/Link/Run → click Build → wait → alt-tab back.

    Total time: compile ~1s + light ~30s-2min + link ~5s (cached).

    EXPERIMENTAL: relies on `bake_lighting` which has a partially-guessed
    CLI invocation. If lighting fails, fall back to compile_map + link_map
    and run the launcher's Light step manually."""
    import time
    started = time.time()
    stages: list[dict] = []

    if not skip_gdtdb:
        s = pipeline.gdtdb_update()
        stages.append({"stage": "gdtdb_update", **s})
        if s.get("returncode", 0) != 0 or s.get("timed_out"):
            return {"failed_at": "gdtdb_update", "stages": stages,
                    "elapsed_seconds": round(time.time() - started, 2)}

    s = pipeline.compile_map(map_name, only_ents=False)
    stages.append({"stage": "compile", **s})
    if s.get("returncode", 0) != 0 or s.get("timed_out"):
        return {"failed_at": "compile", "stages": stages,
                "elapsed_seconds": round(time.time() - started, 2)}

    s = pipeline.bake_lighting(map_name, quality=quality)
    stages.append({"stage": "bake_lighting", **s})
    if s.get("returncode", 0) != 0 or s.get("timed_out"):
        return {"failed_at": "bake_lighting", "stages": stages,
                "elapsed_seconds": round(time.time() - started, 2),
                "note": "Lighting bake failed — try running compile_map + "
                        "link_map only, and use the launcher's Light step."}

    s = pipeline.link(map_name)
    stages.append({"stage": "link", **s})
    if s.get("returncode", 0) != 0 or s.get("timed_out"):
        return {"failed_at": "link", "stages": stages,
                "elapsed_seconds": round(time.time() - started, 2)}

    return {
        "status": "complete",
        "stages": stages,
        "elapsed_seconds": round(time.time() - started, 2),
        "map_name": map_name,
        "fastfile": str(paths.root() / "usermaps" / map_name / "zone" /
                        f"{map_name}.ff"),
    }


# --- Entry point -----------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
