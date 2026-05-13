"""Canonical "playable from scratch" recipe — encodes the v3 invariants.

This is the source of truth for "produce a playable BO3 zombie map from a
prompt." It does NOT clone `zm_demo_v3.map`; it builds a NEW map from
scratch using the v3-verified pattern. Every callable invariant is enforced
in code, and the returned `playable_contract` dict declares what was applied
so the caller can verify nothing was silently dropped.

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

from typing import Any, Literal

from . import geometry, scaffold, zm

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

    # ── 2. Three carved rooms (matches zm_demo_v3 dimensions exactly).
    #    start_zone  [-512..-128, -256..256, 0..256]   south + north barricade windows; east doorway to arena
    #    arena_zone  [-128.. 768, -512..512, 0..384]   west + east doorways
    #    vault_zone  [ 768..1280, -256..256, 0..256]   west doorway from arena
    #
    # Window invariant: width=64, height=64, bottom=48 (= z=64 = waist height).
    # Doorway invariant: width=80, height=96 to match p7_zm_der_door_buy_std_onepiece model.
    geometry.carve_room_with_openings(
        map_name,
        mins=(-512, -256, 0), maxs=(-128, 256, 256),
        openings=[
            {"side": "east",  "width": 80, "height": 96},
            {"side": "south", "width": 64, "height": 64, "bottom": 48},
            {"side": "north", "width": 64, "height": 64, "bottom": 48},
        ],
        wall_thickness=16,
    )
    geometry.carve_room_with_openings(
        map_name,
        mins=(-128, -512, 0), maxs=(768, 512, 384),
        openings=[
            {"side": "west", "width": 80, "height": 96},
            {"side": "east", "width": 80, "height": 96},
        ],
        wall_thickness=16,
    )
    geometry.carve_room_with_openings(
        map_name,
        mins=(768, -256, 0), maxs=(1280, 256, 256),
        openings=[{"side": "west", "width": 80, "height": 96}],
        wall_thickness=16,
    )
    summary["steps"].append({"rooms_carved": 3})

    # ── 3. Outdoor courtyards (REQUIRED for barricade-window risers — they
    # need a walkable exterior surface or the spawn struct sits in void).
    geometry.add_outdoor_courtyard(
        map_name,
        mins=(-512, -432, 0), maxs=(-128, -256, 256),
        open_side="north",  # adjoins start_zone south wall
    )
    geometry.add_outdoor_courtyard(
        map_name,
        mins=(-512, 256, 0), maxs=(-128, 432, 256),
        open_side="south",  # adjoins start_zone north wall
    )
    # Cool-blue accent lights in each courtyard so zombies are visible through
    # the barricade boards (the player can see them coming).
    zm.add_light(map_name, origin=(-320, -344, 200),
                 color=(0.85, 0.9, 1.0), radius=320, stops=4.0)
    zm.add_light(map_name, origin=(-320, 344, 200),
                 color=(0.85, 0.9, 1.0), radius=320, stops=4.0)
    summary["steps"].append({"courtyards_built": 2, "courtyard_lights": 2})

    # ── 4. Register zones (auto-edits init_zones[] in the GSC + sets
    # default_start_location for the starting zone).
    zm.add_zombie_zone(
        map_name, "start_zone",
        volume_center=(-320, 0, 128), volume_size=(384, 512, 256),
        is_starting_zone=True,
    )
    zm.add_zombie_zone(
        map_name, "arena_zone",
        volume_center=(320, 0, 192), volume_size=(896, 1024, 384),
    )
    zm.add_zombie_zone(
        map_name, "vault_zone",
        volume_center=(1024, 0, 128), volume_size=(512, 512, 256),
    )
    summary["steps"].append({"zones_registered": 3})

    # ── 5. Zone graph — buyable doors auto-wire add_adjacent_zone in zone_init.
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

    # ── 6. Player spawn (replaces scaffold placeholder). z=32 = floor+16,
    # canonical 16 units above floor surface so player falls onto floor.
    zm.add_player_spawn(map_name, origin=(-320, 0, 32), angles=(0, 0, 0))
    summary["steps"].append({"player_spawn": (-320, 0, 32)})

    # ── 7. start_zone — barricade windows (NOT interior spawners). Risers sit
    # in courtyards 96 units outside the wall (add_zombie_window default offset).
    start_wall_buys = [
        {"weapon": "pistol_burst", "origin": (-494, -100, 8), "angles": (0, 270, 0)},
    ] if include_wall_buys else []
    zm.furnish_zone(
        map_name, "start_zone",
        wall_buys=start_wall_buys,
        light_origins=[(-320, 0, 200)],
        light_color=(1.0, 0.95, 0.85),
        light_radius=320, light_stops=4.0,
    )
    zm.add_zombie_window(map_name, origin=(-320, -240, 16), yaw=180, zone_name="start_zone")
    zm.add_zombie_window(map_name, origin=(-320, 240, 16), yaw=0,   zone_name="start_zone")
    summary["steps"].append({"start_zone_furnished": True, "windows": 2})

    # ── 8. arena_zone — interior spawn_locations (verified working for
    # SECONDARY zones; do NOT use this pattern in the starting zone). Spawners
    # at z=16 — at floor surface, NEVER higher.
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

    # ── 9. vault_zone — 2 interior spawners + light.
    zm.furnish_zone(
        map_name, "vault_zone",
        spawner_origins=[(810, -200, 16), (810, 200, 16)],
        light_origins=[(1024, 0, 200)],
        light_color=(1.0, 0.85, 0.6),
        light_radius=384, light_stops=4.5,
    )
    summary["steps"].append({"vault_zone_furnished": True})

    # ── 10. Optional vault-zone gameplay objects.
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

    # ── 11. Lighting kit (sky shell + sun + umbra + fpstool). REQUIRED
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
