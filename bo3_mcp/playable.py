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

from . import geometry, mapfile, paths, scaffold, zm

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

    # Spawn struct Z values — should sit at floor surface (z=16 conventionally,
    # or matching the underlying brush top); flag any that are obviously
    # floating (z > 32 in an enclosed room).
    floating_structs = []
    for ent in spawn_structs:
        origin = ent.kvps.get("origin", "")
        parts = origin.split()
        if len(parts) == 3:
            try:
                z = float(parts[2])
                if z > 32:
                    floating_structs.append((ent.guid, z))
            except ValueError:
                pass
    checks.append(_check(
        "spawners:no floating spawn structs (z<=32)",
        len(floating_structs) == 0,
        "all at floor-level" if not floating_structs else f"FLOATING: {floating_structs}",
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

