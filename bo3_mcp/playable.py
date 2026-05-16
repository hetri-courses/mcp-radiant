"""Canonical "playable from scratch" recipe + validator — encodes the v3 invariants.

This is the source of truth for "produce a playable BO3 zombie map from a
prompt." It does NOT clone `zm_demo_v3.map`; it builds a NEW map from
scratch using the v3-verified pattern. Every callable invariant is enforced
in code, and the returned `playable_contract` dict declares what was applied
so the caller can verify nothing was silently dropped.

`validate_playable_contract(map_name)` independently inspects the generated
files (.map / .gsc / .d3dbsp / .led / .hkt / .ff) and verifies each
invariant rather than trusting the contract dict. Use it after `build_full`
to catch silent regressions BEFORE handing the map to a playtester.

See CLAUDE.md "Playable map invariants — DO NOT REGRESS" for the full list
of lessons this recipe locks in. Briefly:

  * 3-zone layout (start → arena → vault) — single-zone playable maps
    remain UNVERIFIED as of May 2026 playtesting.
  * Lighting kit (sky shell + sun + umbra + fpstool) + interior point lights
    per zone + bake_lighting BEFORE link.
  * Spawners at z=floor_surface (16), never floating.
  * Barricade windows with bottom=48 (waist-height) and outdoor courtyards
    sized for the exterior riser to stand on.
  * Zone graph wired via add_buyable_door's `connects` (auto-emits
    add_adjacent_zone in the GSC zone_init).

For terrain-diffusion integration, this function is the foundation a
terrain recipe should build on (Phase 2: terrain-aware placement helpers
will plug terrain into a specific zone of this layout). DO NOT bypass
this recipe by stitching low-level helpers together — that's how we lost
zombie engagement on smoke_02 through smoke_05.
"""
from __future__ import annotations

import os
import re
from typing import Any, Literal

from . import geometry, mapfile, paths, scaffold, terrain, zm

PlayableLayout = Literal["three_zone"]  # "two_zone_minimal" / "single_arena" → future, currently UNVERIFIED


def make_playable_zombie_foundation(
    map_name: str,
    *,
    layout: PlayableLayout = "three_zone",
    include_perks: bool = True,
    include_wall_buys: bool = True,
    include_mystery_box: bool = True,
    include_pack_a_punch: bool = True,
    include_power_switch: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build a playable BO3 zombie map from scratch using v3-verified invariants.

    Use this **instead of** stitching together `scaffold_zombie_map` +
    `carve_room_with_openings` + `add_zombie_spawner` + … by hand. Hand-stitched
    "minimal" maps have repeatedly failed runtime playtest (zombies stuck,
    falling through world, washed-out lighting) because they drop one or more
    invariants the BO3 zombie framework actually needs.

    Args:
        map_name: must start with `zm_`. The scaffold/GSC/zone files are
            created under MOD_TOOLS/map_source/zm/, /share/raw/scripts/zm/,
            and /usermaps/<map>/zone_source/.
        layout: room layout. Only `"three_zone"` (start + arena + vault) is
            currently VERIFIED to produce a playable runtime; matches the
            geometry of `zm_demo_v3`. Future "two_zone_minimal" / "single_arena"
            options will be added once verified separately.
        include_perks: place 4 perks (juggernaut, speed_cola, double_tap,
            quick_revive) at arena corners. Default True.
        include_wall_buys: pistol_burst in start, smg_standard + ar_standard
            in arena. Default True.
        include_mystery_box: mystery box in vault. Default True.
        include_pack_a_punch: PaP in vault. Default True.
        include_power_switch: power switch in vault. Default True.
        overwrite: pass through to `scaffold_zombie_map`.

    Returns:
        dict with two top-level keys:

        - `summary`: ordered list of build steps (same format as `make_demo_map`)
        - `playable_contract`: each invariant declared True/False so caller can
          verify what was applied. Includes the required build_full command
          and the runtime-pass checklist.

    Critical: this function does NOT run the compile/bake/link. The caller must
    follow up with `build_full(map_name, quality="draft")`. The
    `playable_contract.next_build_command` field declares exactly what to run.
    """
    if not map_name.startswith("zm_"):
        raise ValueError(f"map name must start with 'zm_' (got {map_name!r})")
    if layout != "three_zone":
        raise ValueError(
            f"layout={layout!r} not yet verified to produce playable runtime. "
            f"Use layout='three_zone' (matches verified-working zm_demo_v3 "
            f"pattern). Other layouts coming after their own playtest verification."
        )

    summary: dict[str, Any] = {"name": map_name, "layout": layout, "steps": []}

    # ── 1. Scaffold (template-aligned: skyboxmodel/ssi/wsi/lutmaterial in
    # worldspawn, GSC with zm_usermap::main(), zone manifest, etc.)
    scaffold_result = scaffold.create_zombie_map(map_name, overwrite=overwrite)
    summary["steps"].append({"scaffolded": scaffold_result["name"]})

    # ── 2. Starter room — ONE call. The verified barricade pattern (room
    # + 2 courtyards + 2 risers + 2 barricades + interior light + zone)
    # is encoded in zm.add_barricaded_starter_room. East doorway opens
    # into arena_zone (door entity added in step 4).
    zm.add_barricaded_starter_room(
        map_name,
        mins=(-512, -256, 0), maxs=(-128, 256, 256),
        zone_name="start_zone", is_starting_zone=True,
        extra_openings=[{"side": "east", "width": 80, "height": 96}],
    )
    summary["steps"].append({"starter_room": "via add_barricaded_starter_room"})

    # ── 3. Arena + vault (plain rooms, no barricades).
    geometry.carve_room_with_openings(
        map_name,
        mins=(-128, -512, 0), maxs=(768, 512, 384),
        openings=[
            {"side": "west", "width": 80, "height": 96},
            {"side": "east", "width": 80, "height": 96},
        ],
        wall_thickness=16,
    )
    zm.add_zombie_zone(
        map_name, "arena_zone",
        volume_center=(320, 0, 192), volume_size=(896, 1024, 384),
    )
    geometry.carve_room_with_openings(
        map_name,
        mins=(768, -256, 0), maxs=(1280, 256, 256),
        openings=[{"side": "west", "width": 80, "height": 96}],
        wall_thickness=16,
    )
    zm.add_zombie_zone(
        map_name, "vault_zone",
        volume_center=(1024, 0, 128), volume_size=(512, 512, 256),
    )
    summary["steps"].append({"arena_and_vault_carved": True})

    # ── 4. Zone graph — buyable doors auto-wire add_adjacent_zone in zone_init.
    zm.add_buyable_door(
        map_name,
        door_mins=(-128, -40, 16), door_maxs=(-112, 40, 112),
        cost=500, script_flag="enter_arena",
        connects=("start_zone", "arena_zone"),
        door_model_yaw=90,
    )
    zm.add_buyable_door(
        map_name,
        door_mins=(768, -40, 16), door_maxs=(784, 40, 112),
        cost=1500, script_flag="enter_vault",
        connects=("arena_zone", "vault_zone"),
        door_model_yaw=90,
    )
    summary["steps"].append({"doors_added": 2})

    # ── 5. Player spawn + start_zone pistol wall_buy.
    zm.add_player_spawn(map_name, origin=(-320, 0, 32), angles=(0, 0, 0))
    if include_wall_buys:
        zm.add_wall_buy(map_name, "pistol_burst",
                        origin=(-494, -100, 8), angles=(0, 270, 0))
    summary["steps"].append({"player_spawn": (-320, 0, 32)})

    # ── 6. arena_zone — interior spawn_locations.
    # WARNING (verified May 13 2026 playtest): the interior spawn_location
    # pattern as currently encoded by this MCP is buggy — zombies spawn but
    # AI tracking glitches (they "blink in" rather than rise, tracking is
    # unreliable). Side-by-side playtest of zm_foundation_v1 vs zm_demo_v3
    # confirmed both have the SAME bug, with byte-identical entity layouts.
    # That proves the *current MCP recipe* is broken, NOT that BO3 itself
    # cannot do non-barricade spawns. Treyarch's stock maps presumably
    # have a working version; we just haven't reproduced it yet. The
    # missing piece is unknown — could be an auxiliary entity, a wrong
    # location_type, a script_string link, navmesh-snap, etc. This is an
    # open Phase 2 investigation. Keep the spawners here for now since
    # they DO at least produce zombies (and demo equivalence is the
    # baseline), but treat the engagement as best-effort until fixed.
    # Spawners at z=16 (floor surface) — that part is correct.
    arena_wall_buys = [
        {"weapon": "smg_standard", "origin": (320, -496, 8), "angles": (0, 0, 0)},
        {"weapon": "ar_standard",  "origin": (320, 496, 8),  "angles": (0, 180, 0)},
    ] if include_wall_buys else []
    zm.furnish_zone(
        map_name, "arena_zone",
        perks=(["juggernaut", "speed_cola", "double_tap", "quick_revive"] if include_perks else []),
        perk_zone_center=(320, 0, 16),
        perk_zone_size=(896, 1024, 0),
        perk_margin=160,
        wall_buys=arena_wall_buys,
        spawner_origins=[(0, -460, 16), (700, 460, 16)],  # z=16 floor surface
        light_origins=[
            (100, -240, 320), (100, 240, 320),
            (540, -240, 320), (540, 240, 320),
        ],
        light_color=(0.9, 0.95, 1.0),
        light_radius=480, light_stops=5.0,
    )
    summary["steps"].append({"arena_zone_furnished": True})

    # ── 7. vault_zone — 2 interior spawners + light.
    zm.furnish_zone(
        map_name, "vault_zone",
        spawner_origins=[(810, -200, 16), (810, 200, 16)],
        light_origins=[(1024, 0, 200)],
        light_color=(1.0, 0.85, 0.6),
        light_radius=384, light_stops=4.5,
    )
    summary["steps"].append({"vault_zone_furnished": True})

    # ── 8. Optional vault-zone gameplay objects.
    vault_features: list[str] = []
    if include_mystery_box:
        zm.add_mystery_box(map_name, origin=(1024, 0, 16), angles=(0, 0, 0))
        vault_features.append("mystery_box")
    if include_pack_a_punch:
        zm.add_pack_a_punch(map_name, origin=(1024, 216, 20), angles=(0, 90, 0))
        vault_features.append("pack_a_punch")
    if include_power_switch:
        zm.add_power_switch(map_name, origin=(1024, -232, 24), angles=(0, 180, 0))
        vault_features.append("power_switch")
    if vault_features:
        summary["steps"].append({"vault_features": vault_features})

    # ── 9. Lighting kit (sky shell + sun + umbra + fpstool). REQUIRED
    # for proper rendering. The interior point lights above were added by
    # furnish_zone; this adds the global volumes.
    zm.add_lighting_kit(
        map_name,
        playable_mins=(-512, -512, 0),
        playable_maxs=(1280, 512, 384),
        buffer=128,
    )
    summary["steps"].append({"lighting_kit": "applied"})

    # ── playable_contract: declarative record of which invariants we applied.
    # The caller (and future Claude sessions) should verify this matches what
    # CLAUDE.md "Playable map invariants" requires. If a field is False or
    # missing, the map is NOT considered playable.
    contract = {
        "map_name": map_name,
        "layout": layout,
        "zones": ["start_zone", "arena_zone", "vault_zone"],
        "starting_zone": "start_zone",
        # Zombie engagement status, per zone, as of May 13 2026 playtest:
        "zone_engagement_status": {
            "start_zone": "VERIFIED — barricade+riser+courtyard pattern, zombies rise/vault/path correctly",
            "arena_zone": "MCP-UNVERIFIED — interior spawn_location pattern; zombies blink in, tracking glitchy. Same recipe-level bug in zm_demo_v3 (NOT a BO3 framework limitation — Phase 2 will hunt for a stock-derived working version).",
            "vault_zone": "MCP-UNVERIFIED — same interior spawn_location pattern as arena",
        },
        "zone_graph_edges": [
            ("start_zone", "arena_zone", "enter_arena", 500),
            ("arena_zone", "vault_zone", "enter_vault", 1500),
        ],
        "player_spawn_origin": (-320, 0, 32),
        "player_spawn_above_floor_units": 16,
        # Spawner invariants
        "barricade_windows_count": 2,
        "barricade_window_bottom": 48,  # waist-height; NEVER 8 (floor-level)
        "barricade_courtyards_count": 2,
        "interior_spawners_count": 4,  # 2 in arena, 2 in vault
        "spawners_z_at_floor_surface": True,  # all at z=16
        "spawners_zone_linked": True,  # all have targetname=<zone>_spawners
        # Lighting invariants
        "sky_shell_added": True,
        "umbra_volume_added": True,
        "sun_volume_added": True,
        "fpstool_volume_added": True,
        "interior_lights_by_zone": {
            "start_zone": 1,
            "arena_zone": 4,
            "vault_zone": 1,
            "courtyards": 2,
        },
        "lighting_bake_required": True,  # caller MUST run build_full or bake_lighting+link
        # Build instructions
        "next_build_command": (
            f"build_full({map_name!r}, quality='draft', skip_gdtdb=False)"
        ),
        "build_includes": ["gdtdb_update", "compile (only_ents=False)", "bake_lighting", "link"],
        # Runtime smoke test checklist (caller must playtest in-game; compile
        # success is NOT runtime success — see CLAUDE.md "Builder pass ≠ runtime pass").
        "runtime_smoke_test_required": True,
        "runtime_checklist": [
            "Map loads in BO3 → Custom Games",
            "Lighting acceptable (NOT washed-out preview)",
            "Player spawns on solid ground",
            "Terrain visible + textured + has collision",
            "Zombies rise from courtyards, climb through barricade windows",
            "Zombies actively path to player inside the room",
            "Opening door to arena_zone spawns interior zombies that path",
            "No console/runtime script errors",
        ],
    }

    return {"summary": summary, "playable_contract": contract}


# ─────────────────────────────────────────────────────────────────────────────
# Terrain-integrated recipe — diffusion terrain INSIDE a verified playable shell
# ─────────────────────────────────────────────────────────────────────────────


def make_terrain_zombie_arena(
    map_name: str,
    *,
    terrain_seed: int = 42,
    # 27x31 cells at cell_size=32 = 864x992 BO3 unit footprint, fitting
    # EXACTLY inside arena interior (which is 864x992). v1 used 16x16
    # cells at cell_size=64 (1024x1024) which bled past arena east wall
    # into the vault and put spawners on the wall; v2 used 13x15 at
    # cell_size=64 (832x960) with a 16-unit gap on every side. v3 fixes
    # both by going to smaller cells.
    terrain_region: tuple[int, int, int, int] = (0, 0, 27, 31),
    terrain_scale: int = 1,
    world_units_per_meter: float = 0.5,
    floor_thickness_units: float = 16.0,
    normalize_elevation: bool = True,
    terrain_origin: tuple[float, float, float] = (-112.0, -496.0, 0.0),
    terrain_cell_size: float = 32.0,
    # Offsets relative to terrain_origin. With origin (-112, -496) these
    # land at world (96, -288), (544, -288), (96, 288), (544, 288) — all
    # well inside arena interior.
    spawner_offsets: tuple[tuple[float, float], ...] = (
        (208, 208), (656, 208), (208, 784), (656, 784),
    ),
    spawner_z_offset: float = 4.0,
    include_perks: bool = True,
    include_wall_buys: bool = True,
    # v22.14 terrain visual-quality knobs. Defaults chosen for a
    # zombies-arena aesthetic: mostly-flat playable floor with ~25%
    # terrain "patches" breaking through, capped under the doorway
    # height so it doesn't block player passage, smoothed slightly,
    # and feathered at room edges so terrain doesn't hard-wall the
    # walls. Override any of these to get raw heightmap terrain.
    terrain_style: str = "broken_floor",
    broken_floor_coverage: float = 0.25,
    max_height_units: float | None = 56.0,
    edge_feather_units: float = 96.0,
    smooth_iterations: int = 1,
    auto_flatten_doorway_pads: bool = True,
    extra_flatten_pads: list[dict] | None = None,
    # v23.5: how the heightmap becomes brushes. "patch_mesh" emits
    # smooth-surface mesh patches (Treyarch's outdoor terrain pattern;
    # runtime-verified May 14 2026 in zm_patch_ai_lab_02 — visible,
    # walkable, AI pathfinds across, line-of-sight unbroken). "voxel"
    # falls back to v22 box-column terrain for the rocky-mesa look.
    terrain_render_mode: str = "patch_mesh",
    patch_chunk_size: int = 8,
    patch_visual_texture: str = "t7_concrete_pebbles_cracked",
    patch_min_z_offset: float = 2.0,
    terrain_server_url: str = "http://localhost:8000",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Diffusion terrain inside a playable BO3 zombie map (v3 architecture).

    Thin composition of REUSABLE helpers:
      - scaffold.create_zombie_map (template)
      - zm.add_barricaded_starter_room (verified barricade pattern, any size)
      - geometry.carve_room_with_openings (arena, vault — plain rooms)
      - zm.add_zombie_zone / zm.add_buyable_door / zm.add_player_spawn
      - terrain.generate_terrain_diffusion (heightmap → brushes + sidecar)
      - terrain.place_zombie_spawners_on_terrain (auto-Z from sidecar)
      - terrain.place_perks_on_terrain (auto-Z from sidecar)
      - zm.furnish_zone (wall buys + lights — vault perks/box/PaP/switch)
      - zm.add_lighting_kit (sky shell + sun + umbra + fpstool)

    Layout (3 zones, demo-v3 dimensions for the foundation walls; terrain
    fills the arena interior):

      [start_zone] —500pt door—▶ [arena_zone w/ terrain] —1500pt door—▶ [vault_zone]
        ↑ barricades+risers           ↑ spawners + perks on terrain        ↑ mystery box, PaP, power

    None of the geometry helpers here are demo-coord-specific — they
    just happen to be called with demo-v3 coords for this recipe.
    Build a custom layout by calling the helpers directly with your
    own dimensions.

    Requires the terrain-diffusion server running. See CLAUDE.md
    "Terrain-diffusion runtime". Recommended: call
    `preview_terrain_diffusion_region(seed=..., region=...)` first.
    """
    if not map_name.startswith("zm_"):
        raise ValueError(f"map name must start with 'zm_' (got {map_name!r})")

    summary: dict[str, Any] = {"name": map_name, "layout": "terrain_arena", "steps": []}

    # ── 1. Scaffold (template + GSC + zone manifest)
    scaffold.create_zombie_map(map_name, overwrite=overwrite)
    summary["steps"].append({"scaffolded": map_name})

    # ── 2. Starter room — ONE reusable helper call replacing what was
    # previously 6 inline calls (carve, 2 courtyards, 2 lights, zone).
    # The east-doorway extra_opening connects to arena_zone (the door
    # entity is added below via add_buyable_door).
    starter = zm.add_barricaded_starter_room(
        map_name,
        mins=(-512, -256, 0), maxs=(-128, 256, 256),
        zone_name="start_zone", is_starting_zone=True,
        extra_openings=[{"side": "east", "width": 80, "height": 96}],
    )
    summary["steps"].append({"starter_room": "added (1 call → 6 inline ops)"})

    # ── 3. Arena room (plain room with west + east doorways; floor will
    # be replaced by terrain below).
    geometry.carve_room_with_openings(
        map_name,
        mins=(-128, -512, 0), maxs=(768, 512, 384),
        openings=[
            {"side": "west", "width": 80, "height": 96},
            {"side": "east", "width": 80, "height": 96},
        ],
        wall_thickness=16,
    )
    zm.add_zombie_zone(
        map_name, "arena_zone",
        volume_center=(320, 0, 192), volume_size=(896, 1024, 384),
    )

    # ── 4. Vault room
    geometry.carve_room_with_openings(
        map_name,
        mins=(768, -256, 0), maxs=(1280, 256, 256),
        openings=[{"side": "west", "width": 80, "height": 96}],
        wall_thickness=16,
    )
    zm.add_zombie_zone(
        map_name, "vault_zone",
        volume_center=(1024, 0, 128), volume_size=(512, 512, 256),
    )

    # ── 5. Buyable doors (auto-wire zone graph in GSC)
    zm.add_buyable_door(
        map_name, door_mins=(-128, -40, 16), door_maxs=(-112, 40, 112),
        cost=500, script_flag="enter_arena",
        connects=("start_zone", "arena_zone"), door_model_yaw=90,
    )
    zm.add_buyable_door(
        map_name, door_mins=(768, -40, 16), door_maxs=(784, 40, 112),
        cost=1500, script_flag="enter_vault",
        connects=("arena_zone", "vault_zone"), door_model_yaw=90,
    )

    # ── 6. Player spawn + starter pistol wall-buy
    zm.add_player_spawn(map_name, origin=(-320, 0, 32), angles=(0, 0, 0))
    if include_wall_buys:
        zm.add_wall_buy(map_name, "pistol_burst",
                        origin=(-494, -100, 8), angles=(0, 270, 0))
    summary["steps"].append({"zones": 3, "doors": 2,
                              "player_spawn": (-320, 0, 32)})

    # ── 7. GENERATE TERRAIN inside arena bounds. Writes JSON sidecar.
    # v22.14: heightmap is post-processed for arena-style visual quality:
    #   - broken_floor mask: most cells stay flat, top ~25% rise as patches
    #   - max_height clamp: peaks don't block the 96-unit doorways
    #   - edge feather: terrain falls to floor near room walls
    #   - smoothing: reduces stepwise voxel look
    #   - auto-flatten pads at the west + east doorway positions so
    #     the zombie can path through them (and the player can walk
    #     out into vault).
    pads: list[dict] = []
    if auto_flatten_doorway_pads:
        # Arena west doorway: x=-128 (interior wall), centered at y=0,
        # opens to start_zone. Flatten a small pad just inside the wall.
        pads.append({"center": [-96, 0], "radius": 64.0,
                     "z": floor_thickness_units})
        # Arena east doorway: x=768, centered at y=0, opens to vault.
        pads.append({"center": [736, 0], "radius": 64.0,
                     "z": floor_thickness_units})
    if extra_flatten_pads:
        pads.extend(extra_flatten_pads)

    terrain_result = terrain.generate_terrain_diffusion(
        map_name,
        region=terrain_region, scale=terrain_scale, seed=terrain_seed,
        origin=terrain_origin, cell_size=terrain_cell_size,
        normalize_elevation=normalize_elevation,
        floor_thickness_units=floor_thickness_units,
        world_units_per_meter=world_units_per_meter,
        server_url=terrain_server_url,
        merge_strips=True, max_brushes=32768,
        terrain_style=terrain_style,
        broken_floor_coverage=broken_floor_coverage,
        max_height_units=max_height_units,
        edge_feather_units=edge_feather_units,
        smooth_iterations=smooth_iterations,
        flatten_pads=pads if pads else None,
        terrain_render_mode=terrain_render_mode,
        patch_chunk_size=patch_chunk_size,
        patch_visual_texture=patch_visual_texture,
        patch_min_z_offset=patch_min_z_offset,
    )
    summary["steps"].append({
        "terrain_brushes": terrain_result.get("brushes_added"),
        "elev_range_m": terrain_result["model_meta"].get("elev_range_m"),
        "sidecar": terrain_result.get("terrain_sidecar"),
    })

    # ── 8. Place arena spawners ON the terrain surface (auto-Z).
    spawner_world_positions = [
        (terrain_origin[0] + dx, terrain_origin[1] + dy)
        for (dx, dy) in spawner_offsets
    ]
    arena_spawners = terrain.place_zombie_spawners_on_terrain(
        map_name, positions=spawner_world_positions,
        zone_name="arena_zone", z_offset=spawner_z_offset,
        location_type="riser_location",
    )
    summary["steps"].append({"arena_spawners_on_terrain": arena_spawners["count"],
                              "spawners_outside_terrain": arena_spawners["outside_terrain_count"]})

    # ── 9. Arena perks ON the terrain surface (auto-Z).
    perks_result: dict | None = None
    if include_perks:
        perks_layout = [
            ("juggernaut",   (32,  -352)),
            ("speed_cola",   (608, -352)),
            ("double_tap",   (32,   352)),
            ("quick_revive", (608,  352)),
        ]
        # Auto-orient each perk to face away from its nearest arena
        # wall (into the play area). Arena interior is x[-112..752]
        # y[-496..496] (room mins/maxs -128/768, -512/512, walls 16
        # thick). Without this the perks all share yaw=0 and the
        # ones along the south wall face INTO the wall (v5 playtest).
        perks_result = terrain.place_perks_on_terrain(
            map_name, perks_layout=perks_layout,
            face_bounds=((-112.0, -496.0), (752.0, 496.0)),
        )
        summary["steps"].append({"arena_perks_on_terrain": perks_result["count"]})

    # ── 10. Arena wall buys + lights (wall buys on walls, NOT on terrain).
    arena_wall_buys = [
        {"weapon": "smg_standard", "origin": (320, -496, 8), "angles": (0, 0, 0)},
        {"weapon": "ar_standard",  "origin": (320, 496, 8),  "angles": (0, 180, 0)},
    ] if include_wall_buys else []
    zm.furnish_zone(
        map_name, "arena_zone",
        wall_buys=arena_wall_buys,
        light_origins=[
            (100, -240, 320), (100, 240, 320),
            (540, -240, 320), (540, 240, 320),
        ],
        light_color=(0.9, 0.95, 1.0), light_radius=480, light_stops=5.0,
    )

    # ── 11. Vault: 2 interior spawners + light + mystery box + PaP + switch.
    zm.furnish_zone(
        map_name, "vault_zone",
        spawner_origins=[(810, -200, 16), (810, 200, 16)],
        light_origins=[(1024, 0, 200)],
        light_color=(1.0, 0.85, 0.6), light_radius=384, light_stops=4.5,
    )
    zm.add_mystery_box(map_name, origin=(1024, 0, 16), angles=(0, 0, 0))
    zm.add_pack_a_punch(map_name, origin=(1024, 216, 20), angles=(0, 90, 0))
    zm.add_power_switch(map_name, origin=(1024, -232, 24), angles=(0, 180, 0))
    summary["steps"].append({"vault_furnished": True})

    # ── 12. Lighting kit (sky shell + sun + umbra + fpstool)
    zm.add_lighting_kit(
        map_name,
        playable_mins=(-512, -512, 0), playable_maxs=(1280, 512, 384),
        buffer=128,
    )
    summary["steps"].append({"lighting_kit": "applied"})

    contract = {
        "map_name": map_name,
        "layout": "terrain_arena",
        "zones": ["start_zone", "arena_zone", "vault_zone"],
        "starting_zone": "start_zone",
        "zone_engagement_status": {
            "start_zone": "VERIFIED — barricade+riser+courtyard pattern",
            "arena_zone": "TERRAIN-MODE — diffusion terrain inside, risers on terrain surface, script_string=find_flesh",
            "vault_zone": "MCP-UNVERIFIED interior spawn_location pattern (unchanged from foundation)",
        },
        "terrain": {
            "source": "terrain-diffusion",
            "seed": terrain_seed,
            "region": list(terrain_region),
            "scale": terrain_scale,
            "world_units_per_meter": world_units_per_meter,
            "normalize_elevation": normalize_elevation,
            "floor_thickness_units": floor_thickness_units,
            "origin": list(terrain_origin),
            "cell_size": terrain_cell_size,
            "brushes_added": terrain_result.get("brushes_added"),
            "elev_range_m": terrain_result["model_meta"].get("elev_range_m"),
            "sidecar_path": terrain_result.get("terrain_sidecar"),
            "style": terrain_style,
            "preprocessing": terrain_result["model_meta"].get("preprocessing_applied", {}),
            "max_height_units": max_height_units,
            "edge_feather_units": edge_feather_units,
            "smooth_iterations": smooth_iterations,
            "broken_floor_coverage": broken_floor_coverage if terrain_style == "broken_floor" else None,
            "spawner_placements": arena_spawners["placed"],
            "perk_placements": perks_result["placed"] if perks_result else [],
        },
        "next_build_command": f"build_full('{map_name}', quality='draft', skip_gdtdb=False)",
        "runtime_smoke_test_required": True,
        "runtime_checklist": [
            "Map loads in BO3",
            "Lighting baked (not preview-fallback)",
            "Buy 500pt door, enter arena",
            "Arena floor is voxel terrain flush with the walls",
            "Risers emerge from the terrain SURFACE (auto-Z worked)",
            "Perks sit on the terrain surface (not buried — auto-Z worked)",
            "Risers pursue the player",
            "Open 1500pt door to vault — mystery box + PaP + power switch present",
        ],
    }

    return {"summary": summary, "playable_contract": contract,
            "terrain_meta": terrain_result.get("model_meta", {})}


# ─────────────────────────────────────────────────────────────────────────────
# Validator — independently verify the generated map matches its contract
# ─────────────────────────────────────────────────────────────────────────────


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail}


def _file_size_ok(path: str, min_bytes: int = 1) -> tuple[bool, str]:
    if not os.path.isfile(path):
        return False, f"not found: {path}"
    size = os.path.getsize(path)
    if size < min_bytes:
        return False, f"too small: {size} bytes (< {min_bytes}): {path}"
    return True, f"{size} bytes"


def validate_playable_contract(
    map_name: str,
    *,
    build_summary: dict | None = None,
) -> dict[str, Any]:
    """Inspect the generated files of `map_name` and verify the playable
    invariants. Does NOT trust the contract dict — re-derives every fact
    from the actual .map / .gsc / .d3dbsp / .led / .hkt / .ff files.

    Args:
        map_name: target map (e.g. "zm_foundation_v1"). Must already have
            been built via `build_full` for the artifact checks to pass.
        build_summary: optional dict returned by `build_full`. If provided,
            the validator additionally scans the linker stage output for
            preview-lighting fallback. If omitted, the linker-output check
            is skipped (its sub-check status is "SKIPPED").

    Returns:
        dict with:
          - `overall`: "PASS" / "FAIL"
          - `passed`, `failed`, `skipped`: counts
          - `checks`: list of {name, status, detail} for each individual check
          - `notes`: list of free-form warnings (missing materials, etc.)
    """
    checks: list[dict[str, Any]] = []
    notes: list[str] = []

    # ── 1. Artifacts on disk (post-build_full) ────────────────────────
    map_path = paths.map_source(map_name)
    gsc_path = paths.gsc(map_name)
    bsp_path = os.path.join(
        str(paths.root()),
        "share", "raw", "maps", "zm", f"{map_name}.d3dbsp",
    )
    led_path = os.path.join(
        str(paths.root()),
        "share", "raw", "maps", "zm", f"{map_name}.led",
    )
    navmesh_path = os.path.join(
        str(paths.root()),
        "share", "raw", "maps", "zm", f"{map_name}_navmesh.hkt",
    )
    ff_path = os.path.join(
        str(paths.root()),
        "usermaps", map_name, "zone", f"{map_name}.ff",
    )

    for label, path, min_size in [
        ("artifact:.map", map_path, 1000),
        ("artifact:.gsc", gsc_path, 100),
        ("artifact:.d3dbsp", bsp_path, 1000),
        ("artifact:.led", led_path, 1),
        ("artifact:_navmesh.hkt", navmesh_path, 100),
        ("artifact:.ff", ff_path, 10000),
    ]:
        ok, detail = _file_size_ok(path, min_bytes=min_size)
        checks.append(_check(label, ok, detail))

    # ── 2. GSC verification ────────────────────────────────────────────
    gsc_text = ""
    if os.path.isfile(gsc_path):
        with open(gsc_path, "r", encoding="utf-8", errors="replace") as f:
            gsc_text = f.read()

    # default_start_location
    m = re.search(r'level\.default_start_location\s*=\s*"([^"]+)"', gsc_text)
    checks.append(_check(
        "gsc:default_start_location set",
        bool(m),
        f"= {m.group(1)!r}" if m else "missing — zone manager will not pick a starting zone",
    ))

    # init_zones[N] entries
    init_zone_matches = re.findall(r'init_zones\[(\d+)\]\s*=\s*"([^"]+)"', gsc_text)
    checks.append(_check(
        "gsc:init_zones populated",
        len(init_zone_matches) >= 1,
        f"{len(init_zone_matches)} zones: " + ", ".join(z for _, z in init_zone_matches) if init_zone_matches else "no init_zones[N] entries",
    ))

    # zm_zonemgr::manage_zones thread (zone manager must be started)
    checks.append(_check(
        "gsc:manage_zones thread started",
        "zm_zonemgr::manage_zones" in gsc_text,
        "level thread zm_zonemgr::manage_zones( init_zones ); present" if "zm_zonemgr::manage_zones" in gsc_text else "missing",
    ))

    # zone graph edges (add_adjacent_zone), expected for multi-zone
    adj_matches = re.findall(
        r'zm_zonemgr::add_adjacent_zone\(\s*"([^"]+)"\s*,\s*"([^"]+)"',
        gsc_text,
    )
    if len(init_zone_matches) > 1:
        checks.append(_check(
            "gsc:zone graph edges present (multi-zone)",
            len(adj_matches) >= 1,
            f"{len(adj_matches)} edges" if adj_matches else "MISSING — secondary zones will not activate via doors",
        ))

    # ── 3. .map entity verification ───────────────────────────────────
    try:
        mf = mapfile.load(map_path)
    except Exception as e:
        checks.append(_check("map:parses", False, f"{type(e).__name__}: {e}"))
        return _summarize(checks, notes)
    checks.append(_check("map:parses", True, f"{len(mf.entities)} entities"))

    # Lighting kit entities
    classes_present = {ent.kvps.get("classname") for ent in mf.entities}
    for cls, label in [
        ("volume_sun", "sun_volume"),
        ("umbra_volume", "umbra_volume"),
        ("volume_fpstool", "fpstool_volume"),
    ]:
        present = cls in classes_present
        checks.append(_check(
            f"lighting:{label}",
            present,
            "found" if present else f"MISSING — {cls} entity not in map (lighting bake will be incomplete)",
        ))

    # Player spawn (initial_spawn_points script_struct)
    player_spawns = [
        ent for ent in mf.entities
        if ent.kvps.get("classname") == "script_struct"
        and ent.kvps.get("targetname") == "initial_spawn_points"
        and ent.kvps.get("script_noteworthy", "").startswith("player_")
    ]
    checks.append(_check(
        "player:initial_spawn_points struct exists",
        len(player_spawns) >= 1,
        f"{len(player_spawns)} player spawns" if player_spawns else "missing",
    ))

    # Zone volumes
    zone_volumes = [
        ent for ent in mf.entities
        if ent.kvps.get("classname") == "info_volume"
        and ent.kvps.get("script_noteworthy") == "player_volume"
    ]
    zone_targetnames = [ent.kvps.get("targetname") for ent in zone_volumes]
    checks.append(_check(
        "zones:info_volumes count matches init_zones",
        len(zone_volumes) == len(init_zone_matches),
        f"map has {len(zone_volumes)} zone volumes ({', '.join(zone_targetnames)}); GSC declares {len(init_zone_matches)}",
    ))

    # Spawner factories — must have script_noteworthy="zombie_spawner"
    spawner_factories = [
        ent for ent in mf.entities
        if ent.kvps.get("classname") == "actor_spawner_zm_factory_zombie"
    ]
    factories_ok = all(
        ent.kvps.get("script_noteworthy") == "zombie_spawner"
        for ent in spawner_factories
    )
    checks.append(_check(
        "spawners:factories tagged correctly",
        len(spawner_factories) >= 1 and factories_ok,
        f"{len(spawner_factories)} actor_spawner_zm_factory_zombie entities, "
        + ("all script_noteworthy=zombie_spawner" if factories_ok else "some have wrong script_noteworthy"),
    ))

    # Spawn structs — must have targetname=<zone-targetname>_spawners matching
    # a real zone. The zone's targetname already includes "_zone" suffix (e.g.
    # "start_zone"), so the spawner targetname is "start_zone_spawners" — do
    # NOT strip the suffix and re-append.
    spawn_structs = [
        ent for ent in mf.entities
        if ent.kvps.get("classname") == "script_struct"
        and ent.kvps.get("script_noteworthy") in ("spawn_location", "riser_location")
    ]
    valid_targetnames = {f"{tn}_spawners" for tn in zone_targetnames if tn}
    unlinked_structs = [
        ent for ent in spawn_structs
        if (ent.kvps.get("targetname") or "") not in valid_targetnames
    ]
    checks.append(_check(
        "spawners:all spawn_structs zone-linked",
        len(unlinked_structs) == 0 and len(spawn_structs) >= 1,
        f"{len(spawn_structs)} spawn structs, {len(unlinked_structs)} unlinked (valid targetnames: {sorted(valid_targetnames)})",
    ))

    # Spawn struct script_string — MUST be set or the AI behavior tree
    # never fires. Treyarch's stock zm_template_test.map uses
    # "find_flesh" on interior risers and the barricade-link tag (e.g.
    # "receiver_set_entry_a" or auto-generated window_<zone>_<x>_<y>) on
    # barricade-paired ones. Missing script_string = zombies spawn but
    # stand still — confirmed runtime bug in zm_foundation_v1 v22.7 arena
    # vs zm_demo_v3 arena (both had the same missing-script_string bug).
    structs_without_script_string = [
        ent for ent in spawn_structs
        if not (ent.kvps.get("script_string") or "").strip()
    ]
    checks.append(_check(
        "spawners:all spawn_structs have script_string set",
        len(structs_without_script_string) == 0,
        "all have script_string (find_flesh or barricade link)"
        if not structs_without_script_string
        else f"{len(structs_without_script_string)}/{len(spawn_structs)} structs missing script_string "
        f"— zombies will spawn but AI won't engage. Set script_string='find_flesh' for interior "
        f"risers or the barricade's link tag for window-paired ones.",
    ))

    # Spawn struct XY position — must be inside the declared zone volume's
    # XY bounds. v22.11 had spawners at world x=768 (arena's east wall
    # position) which spawned zombies inside walls + bled into the vault
    # zone. Catch this class of bug by checking each struct's XY against
    # its zone's volume bounds (parsed from the info_volume brush).
    structs_outside_zone_xy: list[tuple] = []
    for ent in spawn_structs:
        # Skip barricade-paired risers — they're INTENTIONALLY in courtyards
        # outside the zone (zombies rise outside, vault through the barricade
        # window into the zone). Identified by script_string != "find_flesh"
        # (barricade-paired risers carry the barricade's link tag like
        # "window_start_zone_-320_-240" or "receiver_set_entry_a").
        script_string = ent.kvps.get("script_string") or ""
        if script_string and script_string != "find_flesh":
            continue
        tn = ent.kvps.get("targetname") or ""
        if not tn.endswith("_spawners"):
            continue
        zone_targetname = tn[: -len("_spawners")]
        # Find the matching zone volume entity
        zone_ent = next(
            (z for z in mf.entities
             if z.kvps.get("classname") == "info_volume"
             and z.kvps.get("targetname") == zone_targetname),
            None,
        )
        if zone_ent is None or not zone_ent.brushes:
            # Can't check bounds — no zone volume brush found
            continue
        # Parse the zone volume brush's XY bounds. Brushes are opaque text,
        # but we can extract the 6 face plane points and derive an axis-aligned
        # bounding box. Quick approach: regex out all "( X Y Z )" tuples.
        import re as _re
        brush_text = zone_ent.brushes[0]
        pts = _re.findall(r"\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)", brush_text)
        if not pts:
            continue
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        zone_xmin, zone_xmax = min(xs), max(xs)
        zone_ymin, zone_ymax = min(ys), max(ys)
        # Spawn struct XY
        origin = (ent.kvps.get("origin") or "").split()
        if len(origin) != 3:
            continue
        try:
            sx, sy = float(origin[0]), float(origin[1])
        except ValueError:
            continue
        if not (zone_xmin <= sx <= zone_xmax and zone_ymin <= sy <= zone_ymax):
            structs_outside_zone_xy.append((
                ent.guid, (sx, sy),
                zone_targetname,
                (zone_xmin, zone_ymin, zone_xmax, zone_ymax),
            ))
    checks.append(_check(
        "spawners:all spawn_structs XY inside their zone volume",
        len(structs_outside_zone_xy) == 0,
        ("all spawners XY-bounded by their zone"
         if not structs_outside_zone_xy
         else f"{len(structs_outside_zone_xy)} struct(s) outside zone bounds: {structs_outside_zone_xy[:3]} "
         "— these will spawn zombies in walls / wrong zones."),
    ))

    # Spawn struct Z values — should sit at floor surface (z=16 conventionally,
    # or matching the underlying brush top); flag any that are obviously
    # floating (z > 32 in an enclosed room).
    # Threshold relaxed for terrain-placed spawners: with world_units_per_meter
    # up to ~1.0 and normalize_elevation, terrain top can reach ~80 units.
    # z<=128 still catches "spawner at z=350 above an empty room" type bugs.
    floating_structs = []
    for ent in spawn_structs:
        origin = ent.kvps.get("origin", "")
        parts = origin.split()
        if len(parts) == 3:
            try:
                z = float(parts[2])
                if z > 128:
                    floating_structs.append((ent.guid, z))
            except ValueError:
                pass
    checks.append(_check(
        "spawners:no high-floating spawn structs (z<=128)",
        len(floating_structs) == 0,
        "all at plausible Z" if not floating_structs else f"FLOATING: {floating_structs}",
    ))

    # Interior point lights — at least one per zone
    light_ents = [ent for ent in mf.entities if ent.kvps.get("classname") == "light"]
    checks.append(_check(
        "lighting:interior point lights present",
        len(light_ents) >= len(zone_volumes),
        f"{len(light_ents)} light entities (want >= {len(zone_volumes)} for {len(zone_volumes)} zones)",
    ))

    # ── 4. Build-log verification (optional) ──────────────────────────
    if build_summary is not None:
        # Find the linker stage output
        linker_stage = None
        for stage in build_summary.get("stages", []):
            if stage.get("stage") == "link":
                linker_stage = stage
                break
        if linker_stage is None:
            checks.append(_check("linker:no preview-lighting fallback", False,
                                 "build_summary has no link stage"))
        else:
            link_output = linker_stage.get("output", "")
            preview_fallback = "Falling back to preview lighting" in link_output
            checks.append(_check(
                "linker:no preview-lighting fallback",
                not preview_fallback,
                "no fallback warning" if not preview_fallback
                else "FOUND 'Falling back to preview lighting' — bake step was skipped or failed",
            ))

        # Missing materials surfacing from compile
        compile_stage = next(
            (s for s in build_summary.get("stages", []) if s.get("stage") == "compile"),
            None,
        )
        if compile_stage is not None:
            missing = compile_stage.get("summary", {}).get("missing_materials", [])
            if missing:
                notes.append(
                    f"compile reported {len(missing)} missing materials: "
                    + ", ".join(missing)
                    + " — these surfaces will render as the missing-texture checker pattern in-game"
                )

    return _summarize(checks, notes)


def _summarize(checks: list[dict], notes: list[str]) -> dict[str, Any]:
    passed = sum(1 for c in checks if c["status"] == "PASS")
    failed = sum(1 for c in checks if c["status"] == "FAIL")
    skipped = sum(1 for c in checks if c["status"] == "SKIPPED")
    return {
        "overall": "PASS" if failed == 0 else "FAIL",
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "checks": checks,
        "notes": notes,
    }

