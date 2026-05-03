"""One-call demo: scaffold + carve + populate a complete playable shell.

Useful as a "show me what this thing does" sample for first-time users, and
as a regression check that the full pipeline composes cleanly. Built on the
v1.2 recipes (`add_lighting_kit`, `furnish_zone`) — a complete map in ~10
high-level calls.

Layout:
  - `start_zone` — small starter room west, player spawn + pistol wall buy
  - `arena_zone` — large central arena, all 4 perks + AR + SMG wall buys
  - `vault_zone` — east room with mystery box + pack-a-punch + power switch

Two buyable doors connect the zones (start->arena 500pts, arena->vault
1500pts), 6 zombie spawners, lights in each room, full lighting kit
(sky shell + sun + umbra + reflection probe + fpstool).
"""

from __future__ import annotations

from . import geometry, scaffold, zm


def make_demo_map(name: str, *, overwrite: bool = False) -> dict:
    """Build a complete playable demo map. Returns a summary of what was placed.

    After this runs:
      1. `compile_map(name, only_ents=False)` produces the BSP.
      2. Open the Mod Tools Launcher, tick the map, click Build (full
         pipeline: compile + light medium + link). Wait for the lighting
         bake (~30s-2min for a small map).
      3. Launch BO3 -> Mods -> Custom Games -> <name>.

    The map should render correctly in-game (walls visible, lights working,
    zombies spawning) thanks to the v1.2 template-aligned scaffold +
    `add_lighting_kit` recipe (sky shell + umbra_volume — without these,
    BO3's BSP compiler skips the "restricting BSP to sky brushes" pass and
    walls render as the skybox texture)."""
    if not name.startswith("zm_"):
        raise ValueError(f"map name must start with 'zm_' (got {name!r})")

    summary: dict = {"name": name, "steps": []}

    # 1. Scaffold the directory tree + GSC + zone manifest + worldspawn KVPs
    # (template-aligned: skyboxmodel, ssi, wsi, lutmaterial, etc.)
    scaffolded = scaffold.create_zombie_map(name, overwrite=overwrite)
    summary["steps"].append({"scaffold": {"created": len(scaffolded["created"])}})

    # 2. Carve three rooms aligned along the X axis with matching doorways:
    #    start  [-512..-128, -256..256, 0..256]    east doorway at y=0
    #    arena  [-128.. 768, -512..512, 0..384]    west + east doorways at y=0
    #    vault  [ 768..1280, -256..256, 0..256]    west doorway at y=0
    geometry.carve_room_with_openings(
        name,
        mins=(-512, -256, 0), maxs=(-128, 256, 256),
        openings=[{"side": "east", "width": 80, "height": 112}],
        wall_thickness=16,
    )
    geometry.carve_room_with_openings(
        name,
        mins=(-128, -512, 0), maxs=(768, 512, 384),
        openings=[
            {"side": "west", "width": 80, "height": 112},
            {"side": "east", "width": 80, "height": 112},
        ],
        wall_thickness=16,
    )
    geometry.carve_room_with_openings(
        name,
        mins=(768, -256, 0), maxs=(1280, 256, 256),
        openings=[{"side": "west", "width": 80, "height": 112}],
        wall_thickness=16,
    )
    summary["steps"].append({"rooms_carved": 3})

    # 3. Register zones (auto-edits init_zones[] in the GSC + sets the
    # default_start_location for the starter zone).
    zm.add_zombie_zone(
        name, "start_zone",
        volume_center=(-320, 0, 128), volume_size=(384, 512, 256),
        is_starting_zone=True,
    )
    zm.add_zombie_zone(
        name, "arena_zone",
        volume_center=(320, 0, 192), volume_size=(896, 1024, 384),
    )
    zm.add_zombie_zone(
        name, "vault_zone",
        volume_center=(1024, 0, 128), volume_size=(512, 512, 256),
    )
    summary["steps"].append({"zones_registered": 3})

    # 4. Buyable doors (auto-wire to zone graph via `connects`).
    zm.add_buyable_door(
        name,
        door_mins=(-128, -40, 16), door_maxs=(-112, 40, 128),
        cost=500, script_flag="enter_arena",
        connects=("start_zone", "arena_zone"),
    )
    zm.add_buyable_door(
        name,
        door_mins=(768, -40, 16), door_maxs=(784, 40, 128),
        cost=1500, script_flag="enter_vault",
        connects=("arena_zone", "vault_zone"),
    )
    summary["steps"].append({"doors_added": 2})

    # 5. Player spawn (replaces scaffold's default placeholder).
    zm.add_player_spawn(name, origin=(-320, 0, 32), angles=(0, 0, 0))
    summary["steps"].append({"player_spawn": "(-320, 0, 32)"})

    # 6. Furnish each zone via the bulk recipe.
    # Z conventions for prefabs:
    #   - Wall buys: z=8 (just above floor surface at z=16... wait, our floor
    #     surface IS at z=16 not z=0 because our floor brush is z[0..16]).
    #     Use z=16 so the prefab base sits ON the floor surface.
    #   - PaP / power switch / perk machines: z=16 (sit on floor, prefab
    #     handles its own height).
    # Wall buy x: 2 units inside the wall thickness so the chalk decal sits
    # on the interior wall face.
    #
    # start_zone — pistol wall buy on WEST wall (interior face x=-496)
    # z=8 (was 16, too high per user; chalk decal extends UP from origin)
    zm.furnish_zone(
        name, "start_zone",
        wall_buys=[
            {"weapon": "pistol_burst", "origin": (-494, -100, 8), "angles": (0, 270, 0)},
        ],
        spawner_origins=[(-460, -200, 32), (-460, 200, 32)],
        light_origins=[(-320, 0, 200)],
        light_color=(1.0, 0.95, 0.85),  # warm white
        light_radius=320, light_stops=4.0,
    )
    summary["steps"].append({"start_zone_furnished": True})

    # arena_zone — 4 perks at corners, 2 wall buys, 2 spawners, 4 lights
    zm.furnish_zone(
        name, "arena_zone",
        perks=["juggernaut", "speed_cola", "double_tap", "quick_revive"],
        perk_zone_center=(320, 0, 16),
        perk_zone_size=(896, 1024, 0),
        perk_margin=160,
        # Arena wall buys: y=±492 (4 units into room from interior face;
        # ±488 was further into room and STILL hidden, so try CLOSER to wall);
        # z=8 to match starter height
        wall_buys=[
            {"weapon": "smg_standard", "origin": (320, -492, 8), "angles": (0, 180, 0)},
            {"weapon": "ar_standard",  "origin": (320, 492, 8),  "angles": (0, 0, 0)},
        ],
        spawner_origins=[(0, -460, 32), (700, 460, 32)],
        light_origins=[
            (100, -240, 320), (100, 240, 320),
            (540, -240, 320), (540, 240, 320),
        ],
        light_color=(0.9, 0.95, 1.0),  # cool blue
        light_radius=480, light_stops=5.0,
    )
    summary["steps"].append({"arena_zone_furnished": True})

    # vault_zone — 2 spawners, 1 light. Mystery box / PaP / power switch
    # are handled separately below since they're not in furnish_zone.
    zm.furnish_zone(
        name, "vault_zone",
        spawner_origins=[(810, -200, 32), (810, 200, 32)],
        light_origins=[(1024, 0, 200)],
        light_color=(1.0, 0.85, 0.6),  # warm yellow
        light_radius=384, light_stops=4.5,
    )
    summary["steps"].append({"vault_zone_furnished": True})

    # 7. Mystery box, pack-a-punch, power switch — vault-zone special items.
    # Each prefab has its OWN native height/offset — DO NOT change them
    # together. Empirically dialed in (May 2026 v3 testing):
    #   - mystery box: z=16 sits flush on floor (origin = base)
    #   - PaP: z=32 sits on floor (origin is BELOW the visible model — z=16
    #     made it float DOWN into the floor, z=32 is the right ground-level);
    #     y=216 (24 from wall surface) so back doesn't clip;
    #     yaw=90 was confirmed correct by user — DO NOT touch
    #   - power switch: z=24, y=-232, yaw=180 — confirmed perfect by user
    zm.add_mystery_box(name, origin=(1024, 0, 16), angles=(0, 0, 0))
    zm.add_pack_a_punch(name, origin=(1024, 216, 32), angles=(0, 90, 0))
    zm.add_power_switch(name, origin=(1024, -232, 24), angles=(0, 180, 0))
    summary["steps"].append({"vault_features": ["mystery_box", "pack_a_punch", "power_switch"]})

    # 8. Lighting kit — sky shell + sun + umbra + reflection probe + fpstool
    # in ONE call. The umbra_volume + sky shell combo is what makes BO3
    # actually render the geometry correctly (without it, walls show as sky).
    zm.add_lighting_kit(
        name,
        playable_mins=(-512, -512, 0),
        playable_maxs=(1280, 512, 384),
        buffer=128,
    )
    summary["steps"].append({"lighting_kit": "applied"})

    summary["next_steps"] = [
        f"Compile: compile_map('{name}', only_ents=False)",
        f"Then in the Mod Tools Launcher: tick {name}, check Compile/Light/Link/Run, click Build.",
        f"Then launch BO3 -> Mods -> Custom Games -> {name}.",
    ]
    return summary
