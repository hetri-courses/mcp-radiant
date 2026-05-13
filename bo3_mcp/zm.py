"""Zombie-mode recipe helpers. Each helper knows which file to mutate (top-level
.map, vending prefab, magicboxes prefab, etc.) and wraps `entities.add_entity`
with the right classname + KVPs to satisfy the GSC framework."""

from __future__ import annotations

from pathlib import Path

from . import brushes, entities, geometry, gsc, mapfile, paths

# Perk slug -> zm_core prefab basename. Aliases included for ergonomics.
PERKS: dict[str, str] = {
    "juggernaut": "vending_juggernaut_struct",
    "juggernog": "vending_juggernaut_struct",
    "jugger": "vending_juggernaut_struct",
    "sleight_of_hand": "vending_sleight_struct",
    "speed_cola": "vending_sleight_struct",
    "sleight": "vending_sleight_struct",
    "quick_revive": "vending_revive_struct",
    "revive": "vending_revive_struct",
    "double_tap": "vending_doubletap_struct",
    "doubletap": "vending_doubletap_struct",
    "deadshot": "vending_deadshot_struct",
    "deadshot_daiquiri": "vending_deadshot_struct",
    "stamin_up": "vending_marathon_struct",
    "marathon": "vending_marathon_struct",
    "mule_kick": "vending_additionalprimaryweapon_struct",
    "additionalprimaryweapon": "vending_additionalprimaryweapon_struct",
    "gobblegum": "vending_bgb_struct",
    "bgb": "vending_bgb_struct",
}

# Wall-weapon slug -> zm_core prefab basename
WALL_WEAPONS: dict[str, str] = {
    "ar_standard": "spawnable_weapon_ar_standard",
    "ar_cqb": "spawnable_weapon_ar_cqb",
    "ar_longburst": "spawnable_weapon_ar_longburst",
    "ar_marksman": "spawnable_weapon_ar_marksman",
    "smg_standard": "spawnable_weapon_smg_standard",
    "smg_fastfire": "spawnable_weapon_smg_fastfire",
    "smg_versatile": "spawnable_weapon_smg_versatile",
    "shotgun_pump": "spawnable_weapon_shotgun_pump",
    "pistol_burst": "spawnable_weapon_pistol_burst",
    "pistol_fullauto": "spawnable_weapon_pistol_fullauto",
    "frag_grenade": "spawnable_weapon_frag_grenade",
    "bouncingbetty": "spawnable_weapon_bouncingbetty",
    "bowie": "spawnable_weapon_bowie",
}


def _load(path: Path) -> mapfile.MapFile:
    if not path.exists():
        raise FileNotFoundError(
            f"target file does not exist: {path}. "
            "Run scaffold_zombie_map first, or check the map name."
        )
    return mapfile.load(path)


def _save(mf: mapfile.MapFile, path: Path) -> None:
    mf.save(path)


def _add_prefab_ref(
    mf: mapfile.MapFile,
    prefab: str,
    origin: tuple[float, float, float],
    angles: tuple[float, float, float],
    *,
    layer: str | None = None,
) -> mapfile.Entity:
    return entities.add_entity(
        mf,
        "misc_prefab",
        origin=origin,
        angles=angles,
        layer=layer,
        kvps={"model": paths.core_prefab_ref(prefab)},
    )


# --- Perks ------------------------------------------------------------------


def add_perk(
    map_name: str,
    perk: str,
    origin: tuple[float, float, float],
    angles: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    """Place a perk machine via misc_prefab into the map's vending prefab.
    Also auto-uncomments the matching `#using` line in the GSC, or inserts it
    if missing, so the perk machine has its backing logic at runtime."""
    perk_key = perk.lower().replace("-", "_").replace(" ", "_")
    if perk_key not in PERKS:
        raise ValueError(
            f"unknown perk {perk!r}. Available: {sorted(set(PERKS))}"
        )
    prefab = PERKS[perk_key]
    target = paths.map_prefab_dir(map_name) / "script" / f"{map_name}_vending.map"
    mf = _load(target)
    entity = _add_prefab_ref(mf, prefab, origin, angles)
    _save(mf, target)

    gsc_action: dict | None = None
    if prefab in gsc.PREFAB_TO_IMPORT:
        gsc_action = gsc.ensure_import(paths.gsc(map_name), gsc.PREFAB_TO_IMPORT[prefab])

    return {
        "perk": perk_key,
        "prefab": prefab,
        "guid": entity.guid,
        "target_file": str(target),
        "origin": origin,
        "angles": angles,
        "gsc_import": gsc_action,
    }


# --- Pack-a-Punch -----------------------------------------------------------


def place_perks_in_zone(
    map_name: str,
    perks: list[str],
    zone_center: tuple[float, float, float],
    zone_size: tuple[float, float, float],
    *,
    z: float | None = None,
    margin: float = 80.0,
) -> dict:
    """Bulk helper: distribute N perks evenly around a zone's interior
    perimeter, facing inward. Each perk goes through `add_perk`, so GSC
    imports are auto-managed for all of them.

    `zone_center`/`zone_size` should match what you passed to `add_zombie_zone`
    (or any rectangular bounds). `margin` is how far inside the wall the perks
    sit; `z` defaults to the zone's center height — pass an explicit z if
    your floor isn't at the zone's vertical center."""
    if not perks:
        raise ValueError("perks list is empty")

    cx, cy, cz = zone_center
    sx, sy, _ = zone_size
    inner_w = sx - 2 * margin
    inner_d = sy - 2 * margin
    if inner_w <= 0 or inner_d <= 0:
        raise ValueError(
            f"margin {margin} is too large for zone size ({sx}, {sy})"
        )
    perimeter = 2 * (inner_w + inner_d)
    base_z = cz if z is None else z

    # Yaw convention (derived from Treyarch's ZM template):
    # The vending/spawnable_weapon prefabs have their "buy face" pointing
    # south (-Y) by default. yaw rotates CCW in plan view. So:
    #   north wall (+Y high) -> yaw=0   (face stays -Y, into room)
    #   east wall  (+X high) -> yaw=90  (rotate 90 CCW, faces -X into room)
    #   south wall (-Y low)  -> yaw=180 (faces +Y into room)
    #   west wall  (-X low)  -> yaw=270 (faces +X into room)
    placed: list[dict] = []
    for i, perk_name in enumerate(perks):
        t = (i / len(perks)) * perimeter
        if t < inner_w:
            # Walking along the SOUTH wall (perk against -Y wall)
            x = cx - inner_w / 2 + t
            y = cy - inner_d / 2
            yaw = 180.0    # face +Y into room
        elif t < inner_w + inner_d:
            # Walking along the EAST wall (perk against +X wall)
            x = cx + inner_w / 2
            y = cy - inner_d / 2 + (t - inner_w)
            yaw = 90.0     # face -X into room
        elif t < 2 * inner_w + inner_d:
            # Walking along the NORTH wall (perk against +Y wall)
            x = cx + inner_w / 2 - (t - inner_w - inner_d)
            y = cy + inner_d / 2
            yaw = 0.0      # face -Y into room (template default)
        else:
            # Walking along the WEST wall (perk against -X wall)
            x = cx - inner_w / 2
            y = cy + inner_d / 2 - (t - 2 * inner_w - inner_d)
            yaw = 270.0    # face +X into room
        placed.append(add_perk(map_name, perk_name, (x, y, base_z), (0.0, yaw, 0.0)))

    return {
        "zone_center": zone_center,
        "zone_size": zone_size,
        "margin": margin,
        "perks_placed": len(placed),
        "perks": [{"perk": p["perk"], "origin": p["origin"], "guid": p["guid"]} for p in placed],
    }


def add_pack_a_punch(
    map_name: str,
    origin: tuple[float, float, float],
    angles: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    """Place pack-a-punch in the vending prefab and ensure the GSC has the
    matching `#using scripts\\zm\\_zm_pack_a_punch;` import."""
    prefab = "vending_weapon_upgrade_spawnable"
    target = paths.map_prefab_dir(map_name) / "script" / f"{map_name}_vending.map"
    mf = _load(target)
    entity = _add_prefab_ref(mf, prefab, origin, angles)
    _save(mf, target)

    gsc_action = gsc.ensure_import(
        paths.gsc(map_name), gsc.PREFAB_TO_IMPORT[prefab]
    )

    return {
        "guid": entity.guid,
        "target_file": str(target),
        "origin": origin,
        "angles": angles,
        "gsc_import": gsc_action,
    }


# --- Mystery Box ------------------------------------------------------------


_MAGIC_BOX_ZBARRIER_KVPS = {
    "type": "zmcore_MagicBox",
    "barrieranimtime": "0",
    "showalternatemodel": "0",
    "showupgradedmodel": "0",
    "zbarriernumboards": "5",
    "zbarrierboardanim1": "o_zombie_magic_box_fake_idle_twitch_a",
    "zbarrierboardanim2": "o_zombie_magic_box_leave",
    "zbarrierboardanim3": "o_zombie_magic_box_close",
    "zbarrierboardanim4": "o_zombie_magic_box_teddy_rise",
    "zbarrierboardanim5": "o_zombie_magic_box_teddy_rise",
    "zbarrierboardmodel1": "p6_anim_zm_magic_box_fake",
    "zbarrierboardmodel2": "p6_anim_zm_magic_box",
    "zbarrierboardmodel3": "p6_anim_zm_magic_box",
    "zbarrierboardmodel4": "tag_origin",
    "zbarrierboardmodel5": "tag_origin",
    "zbarriertearanim1": "o_zombie_magic_box_fake_idle_twitch_b",
    "zbarriertearanim2": "o_zombie_magic_box_arrive",
    "zbarriertearanim3": "o_zombie_magic_box_open",
    "zbarriertearanim4": "o_zombie_magic_box_weapon_rise",
    "zbarriertearanim5": "o_zombie_magic_box_weapon_dual_rise",
}


def add_mystery_box(
    map_name: str,
    origin: tuple[float, float, float],
    angles: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    chest_name: str = "start_chest",
    is_starting: bool = True,
    cost: int = 950,
) -> dict:
    """Place a complete mystery box: cinder-block prefab (visual base) +
    `zbarrier_zmcore_MagicBox` (the animated chest model with all open/teddy/
    weapon-rise animations) + `script_struct` (the spawn anchor the GSC
    framework finds via `struct::get_array("treasure_chest_use", "targetname")`).

    Without these three together the box won't appear in-game — the prefab
    alone is just cinder bricks (`p7_cinder_block`), no chest. Empirically
    confirmed against `_prefabs/zm/zm_giant/script/zm_giant_magicboxes.map`
    which uses this exact triple per location.

    On `is_starting=True` (default), patches the GSC to set
    `level.start_chest_name` and `level.enable_magic = true` so the framework
    actually initializes the box at this location at game start. For
    additional teleport destinations call with `is_starting=False` and a
    unique `chest_name` (e.g. "chest1", "chest2")."""
    target = paths.map_prefab_dir(map_name) / "script" / f"{map_name}_magicboxes.map"
    mf = _load(target)

    # 1. Visual base (cinder blocks)
    prefab_entity = _add_prefab_ref(mf, "buyable_magic_box_start", origin, angles)

    # 2. Animated chest model (zbarrier with magic_box anims)
    zbarrier_entity = entities.add_entity(
        mf,
        "zbarrier_zmcore_MagicBox",
        origin=origin,
        angles=angles,
        kvps={
            **_MAGIC_BOX_ZBARRIER_KVPS,
            "script_noteworthy": f"{chest_name}_zbarrier",
        },
    )

    # 3. Script struct (spawn anchor)
    struct_entity = entities.add_entity(
        mf,
        "script_struct",
        origin=origin,
        angles=angles,
        kvps={
            "targetname": "treasure_chest_use",
            "script_noteworthy": chest_name,
            "zombie_cost": str(cost),
        },
    )
    _save(mf, target)

    # 4. GSC: enable magic box system + name the starter chest
    gsc_actions = []
    if is_starting:
        gsc_actions.append(gsc.set_level_var(
            paths.gsc(map_name), "start_chest_name", f'"{chest_name}"'
        ))
        gsc_actions.append(gsc.set_level_var(
            paths.gsc(map_name), "enable_magic", "true"
        ))

    return {
        "prefab_guid": prefab_entity.guid,
        "zbarrier_guid": zbarrier_entity.guid,
        "struct_guid": struct_entity.guid,
        "target_file": str(target),
        "origin": origin,
        "angles": angles,
        "chest_name": chest_name,
        "is_starting": is_starting,
        "gsc_actions": gsc_actions,
    }


# --- Power switch -----------------------------------------------------------


def add_power_switch(
    map_name: str,
    origin: tuple[float, float, float],
    angles: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    """Place the power switch in the top-level map. Includes a built-in
    `trigger_use` with `targetname "use_elec_switch"` (recognized by the
    GSC framework)."""
    target = paths.map_source(map_name)
    mf = _load(target)
    entity = _add_prefab_ref(mf, "power_switch", origin, angles)
    _save(mf, target)
    return {
        "guid": entity.guid,
        "target_file": str(target),
        "origin": origin,
        "angles": angles,
    }


# --- Wall buys --------------------------------------------------------------


def add_wall_buy(
    map_name: str,
    weapon: str,
    origin: tuple[float, float, float],
    angles: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    """Place a wall-buy weapon spawnable in the weapons prefab."""
    weapon_key = weapon.lower().replace("-", "_").replace(" ", "_")
    if weapon_key not in WALL_WEAPONS:
        raise ValueError(
            f"unknown wall weapon {weapon!r}. Available: {sorted(WALL_WEAPONS)}"
        )
    prefab = WALL_WEAPONS[weapon_key]
    target = paths.map_prefab_dir(map_name) / "script" / f"{map_name}_weapons.map"
    mf = _load(target)
    entity = _add_prefab_ref(mf, prefab, origin, angles)
    _save(mf, target)
    return {
        "weapon": weapon_key,
        "prefab": prefab,
        "guid": entity.guid,
        "target_file": str(target),
        "origin": origin,
        "angles": angles,
    }


# --- Player spawn -----------------------------------------------------------


def add_player_spawn(
    map_name: str,
    origin: tuple[float, float, float],
    angles: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    player_slot: int = 1,
) -> dict:
    """Set the player spawn for `player_slot` (1-4). Replaces any existing
    `initial_spawn_points` script_struct tagged `player_<slot>` — the scaffold
    template ships with a placeholder at (0, 0, 32) that this call overwrites,
    so calling `add_player_spawn` once per player gives the canonical spawn.

    Without replacement, multiple `initial_spawn_points` script_structs all
    tagged `player_1` would coexist, and the GSC framework picks the
    lowest-index entity — which is usually the scaffold placeholder, not your
    intended spawn. The result is the player spawning inside whatever wall
    happens to be at (0, 0, 32) and dying on map load."""
    if player_slot not in (1, 2, 3, 4):
        raise ValueError("player_slot must be 1-4")
    target = paths.map_source(map_name)
    mf = _load(target)

    # Remove any existing initial_spawn_points for this slot so callers don't
    # end up with stale duplicates from the scaffold template.
    slot_tag = f"player_{player_slot}"
    removed = []
    for existing in list(mf.entities):
        if (
            existing.classname == "script_struct"
            and existing.kvps.get("targetname") == "initial_spawn_points"
            and existing.kvps.get("script_noteworthy") == slot_tag
        ):
            mf.entities.remove(existing)
            removed.append(existing.guid)

    entity = entities.add_entity(
        mf,
        "script_struct",
        origin=origin,
        angles=angles,
        layer="000_Global/ALWAYS COMPILE",
        kvps={
            "targetname": "initial_spawn_points",
            "script_noteworthy": slot_tag,
            "_color": "1 0 0",
        },
    )
    _save(mf, target)
    return {
        "guid": entity.guid,
        "target_file": str(target),
        "origin": origin,
        "player_slot": player_slot,
        "replaced_guids": removed,
    }


# --- Zone -------------------------------------------------------------------


# --- Zombie spawners --------------------------------------------------------


def add_zombie_spawner(
    map_name: str,
    origin: tuple[float, float, float],
    angles: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    zone_name: str | None = None,
    count: int = 9999,
    location_type: str = "spawn_location",
    script_string: str | None = None,
) -> dict:
    """Place a zombie spawner — TWO entities:

    1. An `actor_spawner_zm_factory_zombie` (the AI factory at `origin`).
    2. A sibling `script_struct` at the same origin, tagged
       `targetname "<zone>_spawners"`, `script_noteworthy "<location_type>"`,
       and `script_string "<script_string>"` (defaults to `"find_flesh"`).

    **`script_string="find_flesh"` is CRITICAL.** Without it, zombies spawn
    but their AI behavior tree is never triggered — they appear and stand
    still, or wander aimlessly. Verified on May 13 2026 by comparing this
    MCP's broken output to Treyarch's stock `zm_template_test.map`, which
    uses `script_string="find_flesh"` on every interior riser_location.
    (Barricade-paired risers use the barricade's link tag instead, e.g.
    `"receiver_set_entry_a"` or the auto-generated `add_zombie_window`
    script_string.)

    Pass `zone_name` (e.g. `"cryo_zone"` — with or without the `_zone`
    suffix) to wire the spawn position to that zone. The framework's
    zone_init reads `zone.volumes[0].target` (which `add_zombie_zone` sets
    to `<zone>_spawners`) and walks the matching script_struct array to
    discover spawn points.

    `location_type` controls how the zombie appears (per `_zm_zonemgr.gsc:368`):
      - `"spawn_location"` — walks in (default, basic zombie)
      - `"riser_location"` — rises from the ground (zm_template_test pattern)
      - `"faller_location"` — falls from above
      - `"custom_spawner_entry"` — script-driven entry

    `script_string` defaults to `"find_flesh"` (the standard pursue-player
    AI behavior). Override only when linking to a barricade prefab or a
    custom GSC handler.

    Pattern verified against `zm_template_test.map` entities 3, 5 (interior
    `riser_location` with `script_string="find_flesh"`) and 27 (actor
    factory). Earlier reference to `zm_giant_nodes.map` may be stale.
    """
    target = paths.map_source(map_name)
    mf = _load(target)

    factory = entities.add_entity(
        mf,
        "actor_spawner_zm_factory_zombie",
        origin=origin,
        angles=angles,
        layer="000_Global/Enemies/Zombies",
        kvps={
            "ALERTONSPAWN": "0",
            "MAKEROOM": "1",
            "SCRIPT_FORCESPAWN": "1",
            "SPAWNER": "1",
            "count": str(count),
            "script_disable_bleeder": "1",
            "script_forcespawn": "1",
            "script_noteworthy": "zombie_spawner",
            "_color": "1 0.25 0",
            "engageMaxDist": "700",
            "engageMinDist": "250",
            "model": "c_zom_test_body1",
            "script_dropammo": "1",
            "sm_active_count_max": "3",
            "sm_active_count_min": "3",
            "spawnflags": "19",
        },
    )

    # Default script_string to "find_flesh" (standard Treyarch pursue-player
    # AI behavior). Override only when linking to a barricade or custom handler.
    effective_script_string = script_string if script_string is not None else "find_flesh"

    location_struct = None
    if zone_name is not None:
        canonical_zone = zone_name if zone_name.endswith("_zone") else f"{zone_name}_zone"
        location_struct = entities.add_entity(
            mf,
            "script_struct",
            origin=origin,
            angles=angles,
            layer="000_Global/Enemies/Zombies",
            kvps={
                "script_noteworthy": location_type,
                "script_string": effective_script_string,
                "targetname": f"{canonical_zone}_spawners",
                "_color": "0.929 0.957 0.365",
            },
        )

    _save(mf, target)
    return {
        "factory_guid": factory.guid,
        "location_struct_guid": location_struct.guid if location_struct else None,
        "target_file": str(target),
        "origin": origin,
        "angles": angles,
        "count": count,
        "zone_linked": zone_name is not None,
        "zone_targetname": (
            f"{canonical_zone}_spawners" if zone_name is not None else None
        ),
        "location_type": location_type if zone_name is not None else None,
        "script_string": effective_script_string if zone_name is not None else None,
    }


# --- Barricades (window boards) ---------------------------------------------


def add_barricade(
    map_name: str,
    origin: tuple[float, float, float],
    angles: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    hide_pieces: bool = False,
) -> dict:
    """Place a wood-board zombie barricade (a window/doorway frame with N
    breakable boards). Zombies tear boards off; players repair them for
    points. The standard early-round zombie chokepoint.

    Uses `_prefabs/zm/zm_core/barricade_reciever_wood.map`. Set
    `hide_pieces=True` to use the variant where torn-off board pieces
    despawn instead of remaining on the floor (cleaner-looking).

    **Important — barricades cause BSP leaks unless the map is sealed.**
    The barricade prefab includes `exterior_goal` and zbarrier entities
    that sit ~50 units BEYOND the wall the barricade is placed against
    (in the direction of `angles` yaw — that's where zombies come from).
    Those entities land in unbounded void around your map, causing
    cod2map64 to report a leak. Fix: call `seal_exterior(map_name, ...)`
    once with the bounds of your playable area, AFTER placing all rooms
    and barricades, BEFORE compiling. This wraps the void in a caulk
    shell, sealing it.

    Angles convention: yaw rotates the prefab's "outward" direction.
    yaw=0 → exterior faces +Y, yaw=90 → +X, yaw=180 → -Y, yaw=270 → -X.
    Set the yaw so the barricade faces the wall it's mounted against —
    i.e. the exterior side points toward the void."""
    prefab = (
        "barricade_reciever_wood_hide_pieces"
        if hide_pieces
        else "barricade_reciever_wood"
    )
    target = paths.map_source(map_name)
    mf = _load(target)
    entity = entities.add_entity(
        mf,
        "misc_prefab",
        origin=origin,
        angles=angles,
        layer="000_Global/Special Items/Barricades",
        kvps={"model": paths.core_prefab_ref(prefab)},
    )
    _save(mf, target)
    return {
        "guid": entity.guid,
        "prefab": prefab,
        "target_file": str(target),
        "origin": origin,
        "angles": angles,
    }


# Yaw → unit outward vector for barricade exterior side. yaw=0 outward
# faces +Y; rotates clockwise from there (per add_barricade convention).
_BARRICADE_OUTWARD: dict[int, tuple[float, float]] = {
    0:   (0, 1),    # +Y
    90:  (1, 0),    # +X
    180: (0, -1),   # -Y
    270: (-1, 0),   # -X
}


def add_zombie_window(
    map_name: str,
    origin: tuple[float, float, float],
    yaw: float,
    *,
    zone_name: str,
    spawn_offset: float = 96.0,
    script_string: str | None = None,
    hide_pieces: bool = False,
) -> dict:
    """Complete window-spawn setup in one call: barricade prefab + matching
    exterior spawn-location script_struct (the riser the framework uses to
    rise zombies from the ground outside, which then walk through the boards).

    This is the pattern from `zm_template_test.map`:
      - barricade prefab at the wall, with `script_string` "tag"
      - script_struct at `tag` AHEAD of the wall (in the prefab's outward
        direction), with `targetname "<zone>_spawners"`,
        `script_noteworthy "riser_location"`, matching `script_string`

    Without this pairing, generic in-room spawners (like add_zombie_spawner
    without barricade context) tend to spawn zombies on top of the spawner
    cube, where they can't path to the navmesh — they glitch in place until
    they die. With a barricade window, zombies rise OUTSIDE the wall, walk
    to the barricade, tear boards off, and enter through the opening. That's
    the canonical zombie-map experience.

    Args:
        origin: where the barricade prefab sits (typically at the wall opening,
            z at floor surface — z=16 for our scaffolded maps).
        yaw: 0/90/180/270 — must match the wall the barricade is mounted on.
            yaw=0 → barricade exterior faces +Y (north wall); 90 → +X (east);
            180 → -Y (south); 270 → -X (west).
        zone_name: the zone this window feeds. Spawn struct gets
            `targetname "<zone>_spawners"`.
        spawn_offset: how far OUTSIDE the wall (in the outward direction) the
            spawn riser sits. Default 96 puts it well clear of the wall thickness.
            zm_template_test uses ~144 between barricade and spawn struct.
        script_string: optional shared identifier linking barricade to spawn
            struct. Auto-generated from origin if not provided.
        hide_pieces: same as add_barricade — use the no-debris prefab variant.

    Remember: barricade-style spawners require `seal_exterior(...)` to wrap
    the playable area, otherwise cod2map64 reports a leak (the spawn struct
    sits in unbounded void). add_lighting_kit calls seal_exterior internally."""
    yaw_int = int(round(yaw)) % 360
    if yaw_int not in _BARRICADE_OUTWARD:
        raise ValueError(
            f"yaw must be 0, 90, 180, or 270 (axis-aligned wall); got {yaw}"
        )
    if script_string is None:
        # Stable per-window identifier (so the same call always produces the
        # same script_string — useful for round-tripping / re-runs).
        script_string = f"window_{zone_name}_{int(origin[0])}_{int(origin[1])}"

    canonical_zone = zone_name if zone_name.endswith("_zone") else f"{zone_name}_zone"

    # 1. Barricade prefab — placed AT the wall opening
    target = paths.map_source(map_name)
    mf = _load(target)
    prefab = (
        "barricade_reciever_wood_hide_pieces"
        if hide_pieces
        else "barricade_reciever_wood"
    )
    barricade_entity = entities.add_entity(
        mf,
        "misc_prefab",
        origin=origin,
        angles=(0.0, float(yaw_int), 0.0),
        layer="000_Global/Special Items/Barricades",
        kvps={
            "model": paths.core_prefab_ref(prefab),
            "script_string": script_string,
        },
    )

    # 2. Riser spawn-location struct — placed OUTSIDE the wall in outward dir
    dx, dy = _BARRICADE_OUTWARD[yaw_int]
    spawn_origin = (
        origin[0] + dx * spawn_offset,
        origin[1] + dy * spawn_offset,
        origin[2],
    )
    # The spawn struct faces back TOWARD the wall (zombies face the way they're
    # going — toward the barricade, not away from it).
    spawn_yaw = (yaw_int + 180) % 360
    spawn_struct = entities.add_entity(
        mf,
        "script_struct",
        origin=spawn_origin,
        angles=(0.0, float(spawn_yaw), 0.0),
        layer="000_Global/Enemies/Zombies",
        kvps={
            "script_noteworthy": "riser_location",
            "script_string": script_string,
            "targetname": f"{canonical_zone}_spawners",
            "_color": "1 0 0",
        },
    )
    _save(mf, target)

    return {
        "barricade_guid": barricade_entity.guid,
        "spawn_struct_guid": spawn_struct.guid,
        "barricade_origin": origin,
        "spawn_origin": spawn_origin,
        "yaw": yaw_int,
        "script_string": script_string,
        "zone": canonical_zone,
        "target_file": str(target),
    }


# --- Chalk decals (wall-buy gun outlines) ----------------------------------


def add_chalk_decal(
    map_name: str,
    material: str,
    origin: tuple[float, float, float],
    angles: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    decalsize: tuple[float, float, float] = (4.0, 64.0, 32.0),
    sort_layer: str = "Grunge",
    sort_enum: int = 14,
) -> dict:
    """Place the chalk gun-outline decal next to a wall buy.

    `material` is the material name (e.g. "t7_zm_chalk_buy_kuda" — see the
    `i_t7_zm_chalk_buy_*` images in the asset DB for available weapons).
    `decalsize` is (depth_into_wall, width, height) in world units; default
    (4, 64, 32) is sized for a typical SMG/AR chalk outline.

    Orientation: angles yaw determines which wall the decal projects toward.
    yaw 0 = +X, 90 = -Y, 180 = -X, 270 = +Y. Pick the yaw so the decal's
    forward axis points INTO the wall it's marking."""
    target = paths.map_source(map_name)
    mf = _load(target)
    entity = entities.add_entity(
        mf,
        "misc_volume_decal",
        origin=origin,
        angles=angles,
        layer="000_Global/Special Items/Weapons",
        kvps={
            "decalEditorSortEnum": "0",
            "decalLayerSort": sort_layer,
            "decalLayerSortEnum": str(sort_enum),
            "decalsize": f"{decalsize[0]:g} {decalsize[1]:g} {decalsize[2]:g}",
            "modeloverridematerial": material,
            "model": "vol_decal_cube",
        },
    )
    _save(mf, target)
    return {
        "guid": entity.guid,
        "target_file": str(target),
        "material": material,
        "origin": origin,
        "angles": angles,
        "decalsize": decalsize,
    }


# --- Lighting ---------------------------------------------------------------


def add_sun_volume(
    map_name: str,
    mins: tuple[float, float, float],
    maxs: tuple[float, float, float],
    *,
    sun_set: str = "default_day",
    shadow_split_distance: int = 2000,
    grid_density: int = 32,
) -> dict:
    """Set up baked lighting — a `volume_sun` brush entity covering `mins → maxs`.
    REQUIRED for the lighting bake (`radiant_modtools.exe +medium`).

    Matches Treyarch's official ZM template (`rex/templates/ZM Mod Level/`)
    — much simpler than what we were building before:
      - NO info_null target (the `ssi`/`ssi1` GDT preset provides sun direction)
      - NO global_fill_color/intensity (template doesn't set these)
      - NO shadowSplitCount (template doesn't set this either)
      - shadowSplitDistance = 2000 (template uses this; we used 900)

    `sun_set = "default_day"` matches the worldspawn `ssi`/`wsi` KVPs that
    `scaffold_zombie_map` writes. Use `"default_night"`/`"default_storm"`/
    etc. for other moods (any GDT preset that ships with BO3)."""
    mins, maxs = brushes.normalize_box(mins, maxs)
    target = paths.map_source(map_name)
    mf = _load(target)

    sun_volume = entities.add_entity(
        mf,
        "volume_sun",
        layer="000_Global/Lighting",
        kvps={
            "ssi": sun_set,
            "ssi1": sun_set,
            "grid_density": str(grid_density),
            "shadowBiasScale": "1",
            "shadowSplitDistance": str(shadow_split_distance),
            "streamLighting": "1",
        },
    )
    sun_volume.brushes.append(brushes.box_brush(mins, maxs, "sun_volume"))

    _save(mf, target)
    return {
        "volume_sun_guid": sun_volume.guid,
        "target_file": str(target),
        "volume_mins": mins,
        "volume_maxs": maxs,
        "sun_set": sun_set,
    }


def add_umbra_volume(
    map_name: str,
    mins: tuple[float, float, float],
    maxs: tuple[float, float, float],
) -> dict:
    """Add an `umbra_volume` brush entity wrapping the playable area.
    REQUIRED for BO3's BSP compiler to properly compute visibility.

    Without `umbra_volume`, the compile says "UMBRA volume NOT set, defaulting
    to million-unit cube" and the resulting BSP doesn't render geometry
    correctly in-game (player sees skybox where walls should be).

    Pair with a sky-textured shell (`seal_exterior` with `texture="sky"`) so
    the umbra+skybox combination triggers cod2map64's "restricting BSP to sky
    brushes" pass — the line we WANT to see in compile output.

    No KVPs needed beyond classname (per Treyarch template). Brush uses the
    `umbra_volume` material (similar to caulk — invisible in-game)."""
    mins, maxs = brushes.normalize_box(mins, maxs)
    target = paths.map_source(map_name)
    mf = _load(target)

    entity = entities.add_entity(
        mf,
        "umbra_volume",
        layer="000_Global/Lighting",
        kvps={},
    )
    entity.brushes.append(brushes.box_brush(mins, maxs, "umbra_volume"))

    _save(mf, target)
    return {
        "guid": entity.guid,
        "target_file": str(target),
        "volume_mins": mins,
        "volume_maxs": maxs,
    }


def add_reflection_probe(
    map_name: str,
    origin: tuple[float, float, float],
    *,
    radius: float = 2048.0,
) -> dict:
    """Add a `reflection_probe` entity — captures local reflections for
    materials that use environment mapping (metals, glass, wet surfaces).

    Treyarch's ZM template ships with one centered probe; small maps work
    fine with just one at the playable area's center. Larger maps benefit
    from multiple probes spread through different rooms."""
    target = paths.map_source(map_name)
    mf = _load(target)
    entity = entities.add_entity(
        mf,
        "reflection_probe",
        origin=origin,
        layer="000_Global/Lighting",
        kvps={
            "radius": f"{radius:g}",
            "ao_power": "2",
            "ao_range": "16",
            "ao_strength": "1",
            "blend_maxs": "3 3 3",
            "blend_mins": "3 3 3",
            "box": "1",
            "client_server": "ClientSide",
            "debugColor": "1 1 1",
            "exploderFade": "1",
            "grid_density": "16",
            "high_detail": "1",
            "in_play_space": "1",
            "name": "probe",
            "placement_offset": "72",
            "size_max": "72 72 72",
            "size_min": "72 72 72",
        },
    )
    _save(mf, target)
    return {
        "guid": entity.guid,
        "target_file": str(target),
        "origin": origin,
        "radius": radius,
    }


def add_volume_fpstool(
    map_name: str,
    mins: tuple[float, float, float],
    maxs: tuple[float, float, float],
) -> dict:
    """Add a `volume_fpstool` brush entity wrapping the playable area.
    Used by the lighting bake's FPS profiling pass; Treyarch's template
    includes one. Not strictly required but improves bake quality reporting."""
    mins, maxs = brushes.normalize_box(mins, maxs)
    target = paths.map_source(map_name)
    mf = _load(target)
    entity = entities.add_entity(
        mf,
        "volume_fpstool",
        layer="000_Global/Lighting",
        kvps={},
    )
    entity.brushes.append(brushes.box_brush(mins, maxs, "caulk"))
    _save(mf, target)
    return {
        "guid": entity.guid,
        "target_file": str(target),
        "volume_mins": mins,
        "volume_maxs": maxs,
    }


def add_light(
    map_name: str,
    origin: tuple[float, float, float],
    *,
    color: tuple[float, float, float] = (1.0, 0.95, 0.85),
    radius: float = 256.0,
    stops: float = 4.0,
    light_def: str = "white_light",
    primary_type: str = "PRIMARY_OMNI",
    probe_only: bool = False,
    bulb_radius: float = 4.0,
    falloff_distance: float = 8.0,
    enable_falloff: bool = True,
    angles: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    """Place an interior light. Sealed indoor rooms (carve_room without sky
    openings) get NO sunlight from the volume_sun bake — the sun can't reach
    them. Without `light` entities, those rooms render near-black with only
    ambient `vcolor` contribution + dynamic muzzle flashes.

    **Position the light INSIDE the hollow interior, NOT inside the ceiling
    brush.** carve_room creates ceiling brushes that occupy the top
    `wall_thickness` units (default 16) of the room's z range. So if your
    room is `mins=(0,0,0), maxs=(400,400,256)` with default thickness, the
    interior z range is [16, 240] — placing a light at z=240 puts it INSIDE
    the ceiling brush, where it cannot illuminate anything. Always place
    lights at z <= maxs.z - wall_thickness - some_clearance (e.g. for a
    256-tall room with 16-thick walls, light_z <= 200 is safe).

    Defaults give a warm 256-unit fluorescent-style omni light, intensity 4
    stops (medium-bright). Tune per room:
      - Small room (256-tall): light_z=200, radius 192-256, stops 3-4
      - Large hall (512-tall): light_z=440, radius 384-512, stops 5-6, 2-3 of them
      - Corridor: radius 200, stops 3, every ~256 units along length

    `color` is RGB 0..1 (1, 0.95, 0.85 = warm white; 0.5, 0.7, 1 = cold blue).
    `stops` is the photographic-stops brightness — each +1 doubles intensity.
    `radius` is the cutoff sphere; light fades over `falloff_distance`.

    `primary_type`: PRIMARY_OMNI (point), PRIMARY_SPOT (cone), PRIMARY_TYPE_NONE.
    `probe_only`: zm_giant uses True (lights only feed reflection probes,
    actual scene lighting comes from sun bouncing in through openings). For
    sealed interiors with no sun access, leave False so the bake actually
    illuminates surfaces."""
    target = paths.map_source(map_name)
    mf = _load(target)

    entity = entities.add_entity(
        mf,
        "light",
        origin=origin,
        angles=angles,
        layer="000_Global/Lighting",
        kvps={
            "PRIMARY_NOSHADOWMAP": "0",
            "PROBE_ONLY": "1" if probe_only else "0",
            "ENABLE_FALLOFF": "1" if enable_falloff else "0",
            "PRIMARY_TYPE": primary_type,
            "_color": f"{color[0]:g} {color[1]:g} {color[2]:g}",
            "def": light_def,
            "radius": f"{radius:g}",
            "stops": f"{stops:g}",
            "bulbRadius": f"{bulb_radius:g}",
            "falloffdistance": f"{falloff_distance:g}",
            "fov_outer": "115",
            "far_edge": "0.5",
            "roundness": "0.5",
            "penumbraRadius": "3",
            "shadowmapScale": "1",
            "shadowUpdate": "Never",
            "lightingstate1": "1",
            "lightingstate2": "1",
            "lightingstate3": "1",
            "lightingstate4": "1",
            "client_server": "ClientSide",
            "name": "light",
            "spawnflags": "66",
        },
    )
    _save(mf, target)
    return {
        "guid": entity.guid,
        "target_file": str(target),
        "origin": origin,
        "color": color,
        "radius": radius,
        "stops": stops,
        "primary_type": primary_type,
        "probe_only": probe_only,
    }


# --- Buyable doors ----------------------------------------------------------


def add_buyable_door(
    map_name: str,
    door_mins: tuple[float, float, float],
    door_maxs: tuple[float, float, float],
    cost: int,
    script_flag: str,
    *,
    connects: tuple[str, str] | None = None,
    door_name: str | None = None,
    slide_vector: tuple[float, float, float] | None = None,
    door_texture: str = "clip",
    door_model: str | None = "p7_zm_der_door_buy_std_onepiece",
    door_model_yaw: float = 0.0,
    door_model_z_offset: float = 0.0,
    trigger_inflate: float = 128.0,
) -> dict:
    """Create a buyable door — Treyarch's full 3-entity pattern from
    `_prefabs/zm/zm_giant/geo/factory_doors.map`:

    1. `script_brushmodel` with `clip` texture (collision; invisible)
    2. `script_model` with a door model (visible appearance) — same
       targetname / DYNAMICPATH / script_string / script_vector as the
       brushmodel so they slide together when bought
    3. `trigger_use` (the buy interaction)

    Default `door_model` is `p7_zm_der_door_buy_std_onepiece` (Treyarch's
    standard one-piece buyable door). Other options seen in zm_giant:
    `p7_zm_der_door_buy_med`, `p7_zm_der_door_buy_lrg_left/right` (paired
    double-door panels), `p7_zm_der_door_buy_std_left/right`. Pass
    `door_model=None` to skip the visible model (collision-only invisible door).

    `door_model_yaw` rotates the model around Z; `door_model_z_offset`
    raises/lowers the model relative to the door brush bottom (use this to
    fine-tune model alignment with the brush).

    `script_flag` is the GSC flag set when the door is bought (e.g.
    "enter_warehouse"); zone init functions can listen on this flag to
    activate the gated zone.

    `slide_vector` defaults to `(0, 0, -door_height)` so the door sinks
    below the floor when bought.

    Pass `connects=("zone_a", "zone_b")` to auto-wire the door to the zone
    graph: a `zm_zonemgr::add_adjacent_zone()` call will be appended to the
    map's `<map>_zone_init` function so that buying the door activates the
    gated zone. Without `connects`, the door entities are placed but you'll
    need to wire the flag handling in GSC by hand."""
    door_mins, door_maxs = brushes.normalize_box(door_mins, door_maxs)
    if door_name is None:
        # Generate a unique door name from the script_flag (most flags are
        # human-readable like "enter_warehouse", which gives "warehouse_door").
        suffix = script_flag.removeprefix("enter_") if script_flag.startswith("enter_") else script_flag
        door_name = f"{suffix}_door"

    if slide_vector is None:
        # Slide DOWN by the door's height so it sinks below the floor,
        # completely out of the playable area. Lateral sliding (the previous
        # default) just pushed the door into the next room where it kept
        # blocking. Down is cleaner — door disappears below the floor brush
        # and doesn't interfere with anything.
        door_height = door_maxs[2] - door_mins[2]
        slide_vector = (0.0, 0.0, -door_height)

    target = paths.map_source(map_name)
    mf = _load(target)

    # 1. The door geometry — script_brushmodel that slides on script_vector
    door_entity = entities.add_entity(
        mf,
        "script_brushmodel",
        layer="000_Global/Special Items/Doors",
        kvps={
            "DYNAMICPATH": "1",
            "script_string": "move",
            "script_vector": f"{slide_vector[0]:g} {slide_vector[1]:g} {slide_vector[2]:g}",
            "targetname": door_name,
            "shadow_casting": "1",
            "spawnflags": "1",
        },
    )
    # Brushmodel uses `clip` (invisible solid) — Treyarch's pattern.
    # The visible appearance comes from a separate script_model entity
    # (added below). Earlier iterations tried visible textures on this
    # brush but had culling issues; the script_model approach is what
    # zm_giant's factory_doors.map uses.
    door_entity.brushes.append(
        brushes.box_brush(door_mins, door_maxs, door_texture)
    )

    # 1b. The visible door — script_model with a real Treyarch door asset.
    # Same targetname / DYNAMICPATH / script_string / script_vector as
    # the brushmodel so the framework moves both together when bought.
    # Origin is at the door's bottom-center so the model sits on the floor.
    door_model_entity = None
    if door_model:
        door_center_x = (door_mins[0] + door_maxs[0]) / 2
        door_center_y = (door_mins[1] + door_maxs[1]) / 2
        door_bottom_z = door_mins[2] + door_model_z_offset
        door_model_entity = entities.add_entity(
            mf,
            "script_model",
            origin=(door_center_x, door_center_y, door_bottom_z),
            angles=(0.0, door_model_yaw, 0.0),
            layer="000_Global/Special Items/Doors",
            kvps={
                "DYNAMICPATH": "1",
                "model": door_model,
                "script_string": "move",
                "script_vector": f"{slide_vector[0]:g} {slide_vector[1]:g} {slide_vector[2]:g}",
                "targetname": door_name,
                "client_server": "ServerSide",
                "modelscale": "1",
                "shadow_casting": "1",
                "spawnflags": "1",
            },
        )

    # 2. The trigger_use — slightly inflated bounds, textured "trigger"
    t_mins = (
        door_mins[0] - trigger_inflate,
        door_mins[1] - trigger_inflate,
        door_mins[2] - trigger_inflate,
    )
    t_maxs = (
        door_maxs[0] + trigger_inflate,
        door_maxs[1] + trigger_inflate,
        door_maxs[2] + trigger_inflate,
    )
    # Trigger needs explicit origin so the framework's prompt visibility check
    # (`DistanceSquared(player.origin, trigger.origin) < 128*128` in
    # `_zm_blockers.gsc:blocker_update_prompt_visibility`) works. Brush
    # entities default to origin (0,0,0); without explicit origin, the prompt
    # only shows when the player is within 128 units of world origin —
    # virtually never.
    trigger_origin = (
        (t_mins[0] + t_maxs[0]) / 2,
        (t_mins[1] + t_maxs[1]) / 2,
        (t_mins[2] + t_maxs[2]) / 2,
    )
    trigger_entity = entities.add_entity(
        mf,
        "trigger_use",
        origin=trigger_origin,
        layer="000_Global/Special Items/Doors",
        kvps={
            "script_flag": script_flag,
            "script_noteworthy": "default",  # explicit to skip framework's auto-set path
            "target": door_name,
            "targetname": "zombie_door",
            "zombie_cost": str(cost),
            "cursorhint": "HINT_ACTIVATE",
        },
    )
    trigger_entity.brushes.append(brushes.box_brush(t_mins, t_maxs, "trigger"))

    _save(mf, target)

    gsc_action: dict | None = None
    if connects is not None:
        if len(connects) != 2:
            raise ValueError(f"connects must be a 2-tuple of zone names; got {connects!r}")
        zone_a, zone_b = connects
        # Lenient on _zone suffix to match add_zombie_zone's behavior
        zone_a = zone_a if zone_a.endswith("_zone") else f"{zone_a}_zone"
        zone_b = zone_b if zone_b.endswith("_zone") else f"{zone_b}_zone"
        gsc_action = gsc.add_adjacent_zone_call(
            paths.gsc(map_name), zone_a, zone_b, script_flag
        )

    result = {
        "door_brushmodel_guid": door_entity.guid,
        "door_model_guid": door_model_entity.guid if door_model_entity else None,
        "door_model": door_model,
        "trigger_guid": trigger_entity.guid,
        "door_name": door_name,
        "script_flag": script_flag,
        "zombie_cost": cost,
        "slide_vector": slide_vector,
        "target_file": str(target),
        "gsc_zone_wire": gsc_action,
    }
    if connects is None:
        result["next_steps"] = [
            "Without `connects`, the door isn't wired to a zone yet. "
            "Either re-call with connects=[zone_a, zone_b], or hand-edit the "
            f"GSC: zm_zonemgr::add_adjacent_zone(<a>, <b>, \"{script_flag}\");",
        ]
    return result


# --- Zone -------------------------------------------------------------------


def add_zombie_zone(
    map_name: str,
    zone_name: str,
    volume_center: tuple[float, float, float],
    volume_size: tuple[float, float, float],
    *,
    is_starting_zone: bool = False,
) -> dict:
    """Create a complete zone: an info_volume with `targetname "<zone>_zone"`
    plus a synthesized caulk brush volume covering the bounds, AND auto-register
    the zone in the GSC's `init_zones[]` array (and as `default_start_location`
    if `is_starting_zone=True`).

    Tag convention (per zm_giant entity 17, _zm_zonemgr.gsc:319) — these are
    REQUIRED, not stylistic:
      - `targetname = "<zone>_zone"` — what the framework looks up via
        `GetEntArray(zone_name, "targetname")` in `zone_init()`. WRONG tagging
        causes `assert( IsDefined( zone.volumes[0] ) )` to fire on map load,
        crashing `add_adjacent_zone` and resulting in "Game Over - 1 Down"
        within a second of spawn.
      - `script_noteworthy = "player_volume"` — constant marker for "this
        info_volume is a player zone" (vs other info_volume uses).
      - `target = "<zone>_spawners"` — links to zombie spawners; spawners
        with `targetname = "<zone>_spawners"` belong to this zone.

    `volume_center` is the center of the volume in world coordinates;
    `volume_size` is the full (width, depth, height) extents. Use the bare
    zone name (no `_zone` suffix) — the suffix is added internally."""
    target = paths.map_source(map_name)
    mf = _load(target)
    canonical_name = zone_name if zone_name.endswith("_zone") else f"{zone_name}_zone"

    cx, cy, cz = volume_center
    sx, sy, sz = volume_size
    if sx <= 0 or sy <= 0 or sz <= 0:
        raise ValueError(f"volume_size must be positive on all axes; got {volume_size}")
    mins = (cx - sx / 2, cy - sy / 2, cz - sz / 2)
    maxs = (cx + sx / 2, cy + sy / 2, cz + sz / 2)

    entity = entities.add_entity(
        mf,
        "info_volume",
        layer="000_Global/Zones/Zombie Zones",
        kvps={
            "targetname": canonical_name,
            "script_noteworthy": "player_volume",
            "target": f"{canonical_name}_spawners",
            "_color": "0 1 0",
        },
    )
    entity.brushes.append(brushes.box_brush(mins, maxs, "caulk"))
    _save(mf, target)

    # Auto-register the zone in the GSC, using the canonical name.
    gsc_path = paths.gsc(map_name)
    init_zones_action = gsc.add_init_zone(gsc_path, canonical_name)
    start_action: dict | None = None
    if is_starting_zone:
        start_action = gsc.set_default_start_location(gsc_path, canonical_name)

    return {
        "guid": entity.guid,
        "target_file": str(target),
        "zone_name": canonical_name,
        "is_starting_zone": is_starting_zone,
        "volume_mins": mins,
        "volume_maxs": maxs,
        "gsc_init_zones": init_zones_action,
        "gsc_default_start_location": start_action,
    }


# --- Recipes (compose multiple primitives) -----------------------------------


def add_lighting_kit(
    map_name: str,
    playable_mins: tuple[float, float, float],
    playable_maxs: tuple[float, float, float],
    *,
    buffer: float = 128.0,
    shell_thickness: float = 32.0,
    sun_set: str = "default_day",
    with_reflection_probe: bool = False,
    reflection_probe_origin: tuple[float, float, float] | None = None,
) -> dict:
    """All-in-one lighting + visibility setup. Composes the 5 entities/brushes
    BO3 needs for proper rendering on a sealed map:

      1. `seal_exterior` with sky texture — defines the world boundary
      2. `add_sun_volume` — sun lighting reference for the bake
      3. `add_umbra_volume` — visibility solver scope (the critical missing
         piece that causes the void bug when omitted)
      4. `add_reflection_probe` — env reflections for shiny materials
      5. `add_volume_fpstool` — bake quality reporting

    All wrap the same playable area (`playable_mins` -> `playable_maxs`)
    with appropriate margins. The shell + sun + umbra + fpstool all use
    `shell_outer_bounds` (playable + buffer + thickness); the reflection
    probe sits at the center of the playable area unless overridden.

    Call this AFTER all rooms/geometry are placed, BEFORE compile. One call
    replaces what previously took 4-5 separate tool calls.

    Returns a dict mapping each component to its result for inspection."""
    playable_mins, playable_maxs = brushes.normalize_box(playable_mins, playable_maxs)

    # 1. Seal the exterior with sky texture (NOT caulk — sky establishes
    # the world boundary that BO3 needs for the "restricting BSP to sky
    # brushes" compile pass).
    shell = geometry.seal_exterior(
        map_name,
        playable_mins,
        playable_maxs,
        buffer=buffer,
        shell_thickness=shell_thickness,
        texture="sky",
    )

    # The sun/umbra/fpstool volumes wrap the entire shell-enclosed space.
    shell_outer_mins = shell["shell_outer_mins"]
    shell_outer_maxs = shell["shell_outer_maxs"]
    # Inset slightly so we're inside the sky shell (not protruding through it).
    inner_margin = max(1.0, shell_thickness / 4)
    inset_mins = (
        shell_outer_mins[0] + inner_margin,
        shell_outer_mins[1] + inner_margin,
        shell_outer_mins[2] + inner_margin,
    )
    inset_maxs = (
        shell_outer_maxs[0] - inner_margin,
        shell_outer_maxs[1] - inner_margin,
        shell_outer_maxs[2] - inner_margin,
    )

    sun = add_sun_volume(map_name, inset_mins, inset_maxs, sun_set=sun_set)
    umbra = add_umbra_volume(map_name, inset_mins, inset_maxs)
    fpstool = add_volume_fpstool(map_name, inset_mins, inset_maxs)

    # Reflection probe — OPTIONAL. The lighting bake (radiant_modtools.exe)
    # is finicky about probe placement: it pairs each placed probe with a
    # baked LED sample, and if the probe is at a position that didn't get a
    # sample, the bake errors out with "Probe in level has no counterpart in
    # LED!" For multi-room maps the safe default is to skip the probe; add
    # it manually per-room only when you've verified the bake handles it.
    probe = None
    if with_reflection_probe:
        if reflection_probe_origin is None:
            reflection_probe_origin = (
                (playable_mins[0] + playable_maxs[0]) / 2,
                (playable_mins[1] + playable_maxs[1]) / 2,
                (playable_mins[2] + playable_maxs[2]) / 2,
            )
        probe = add_reflection_probe(map_name, reflection_probe_origin)

    return {
        "playable_mins": playable_mins,
        "playable_maxs": playable_maxs,
        "shell": shell,
        "sun_volume": sun,
        "umbra_volume": umbra,
        "volume_fpstool": fpstool,
        "reflection_probe": probe,
    }


def furnish_zone(
    map_name: str,
    zone_name: str,
    *,
    perks: list[str] | None = None,
    perk_zone_center: tuple[float, float, float] | None = None,
    perk_zone_size: tuple[float, float, float] | None = None,
    perk_margin: float = 80.0,
    wall_buys: list[dict] | None = None,
    spawner_origins: list[tuple[float, float, float]] | None = None,
    light_origins: list[tuple[float, float, float]] | None = None,
    light_color: tuple[float, float, float] = (1.0, 0.95, 0.85),
    light_radius: float = 320.0,
    light_stops: float = 4.0,
) -> dict:
    """Bulk-furnish a zone with perks, wall buys, spawners, and lights in one
    call. Composes `place_perks_in_zone`, `add_wall_buy`, `add_zombie_spawner`,
    `add_light` — each per-item config is optional, omit lists to skip.

    Args:
      perks: list of perk slugs (e.g. `["juggernaut", "speed_cola"]`).
        If provided, requires `perk_zone_center` and `perk_zone_size` for
        perimeter placement.
      wall_buys: list of `{"weapon": slug, "origin": [x,y,z], "angles":
        [pitch,yaw,roll]}` dicts. `angles` defaults to (0, 0, 0).
      spawner_origins: list of spawner positions; each gets zone-linked to
        `zone_name`.
      light_origins: list of light positions. Same color/radius/stops applied
        to all (use individual `add_light` calls if you need varied lights).

    Returns a dict summarizing what was placed."""
    summary: dict = {"zone_name": zone_name, "placed": {}}

    if perks:
        if perk_zone_center is None or perk_zone_size is None:
            raise ValueError(
                "perks requires both perk_zone_center and perk_zone_size"
            )
        result = place_perks_in_zone(
            map_name, perks,
            zone_center=perk_zone_center,
            zone_size=perk_zone_size,
            margin=perk_margin,
        )
        summary["placed"]["perks"] = result.get("perks_placed", len(perks))

    if wall_buys:
        wb_count = 0
        for wb in wall_buys:
            origin = tuple(wb["origin"])
            angles = tuple(wb.get("angles", (0.0, 0.0, 0.0)))
            add_wall_buy(map_name, wb["weapon"], origin, angles)
            wb_count += 1
        summary["placed"]["wall_buys"] = wb_count

    if spawner_origins:
        for o in spawner_origins:
            add_zombie_spawner(map_name, origin=tuple(o), zone_name=zone_name)
        summary["placed"]["spawners"] = len(spawner_origins)

    if light_origins:
        for o in light_origins:
            add_light(
                map_name, origin=tuple(o),
                color=light_color, radius=light_radius, stops=light_stops,
            )
        summary["placed"]["lights"] = len(light_origins)

    return summary
