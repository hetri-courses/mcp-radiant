"""Create a new zombie map's directory tree + template files.

Mirrors the structure that the Mod Tools Launcher's "Add Map" produces, plus
the conventions zm_giant uses (per-concern prefabs for vending/magicboxes,
GSC calling zm_usermap::main(), zone manifest with the right asset entries).
"""

from __future__ import annotations

from pathlib import Path

from . import mapfile, paths


def create_zombie_map(name: str, *, overwrite: bool = False) -> dict:
    """Scaffold all the files a custom zombie map needs.

    The user opens the resulting `<name>.map` in Radiant to sculpt geometry.
    All other content (entities, scripts, zone) is then driven by the MCP."""
    if not name.startswith("zm_"):
        raise ValueError(f"map name must start with 'zm_' (got {name!r})")
    if not name.replace("_", "").isalnum():
        raise ValueError(f"invalid characters in map name {name!r}")

    created: list[Path] = []
    skipped: list[Path] = []

    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not overwrite:
            skipped.append(path)
            return
        path.write_text(content, encoding="utf-8", newline="")
        created.append(path)

    # Top-level .map (worldspawn + spawn point + prefab refs to vending/magicboxes)
    _write(paths.map_source(name), _top_level_map_template(name))

    # Per-concern prefabs (start empty; populated by add_perk / add_mystery_box etc)
    prefab_root = paths.map_prefab_dir(name)
    _write(prefab_root / "script" / f"{name}_vending.map", _empty_prefab_template())
    _write(prefab_root / "script" / f"{name}_magicboxes.map", _empty_prefab_template())
    _write(prefab_root / "script" / f"{name}_weapons.map", _empty_prefab_template())

    # GSC + CSC
    _write(paths.gsc(name), _gsc_template(name))
    _write(paths.csc(name), _csc_template(name))

    # Zone manifest
    _write(paths.zone_manifest(name), _zone_manifest_template(name))

    return {
        "name": name,
        "created": [str(p) for p in created],
        "skipped": [str(p) for p in skipped],
        "next_steps": [
            f"Open {paths.map_source(name)} in Radiant and sculpt geometry.",
            "Use add_player_spawn / add_perk / add_mystery_box / add_pack_a_punch etc. to populate.",
            f"When ready: gdtdb_update -> compile_map({name!r}) -> link_map({name!r}).",
        ],
    }


# --- Templates --------------------------------------------------------------


def _top_level_map_template(map_name: str) -> str:
    """Minimal worldspawn + initial player spawn + refs to per-concern prefabs."""
    worldspawn_guid = mapfile.new_guid()
    spawn_marker_guid = mapfile.new_guid()
    info_player_guid = mapfile.new_guid()
    vending_ref_guid = mapfile.new_guid()
    box_ref_guid = mapfile.new_guid()
    weapons_ref_guid = mapfile.new_guid()

    return f"""iwmap 4
"script_startingnumber" 0
"000_Global" flags expanded  active
"000_Global/ALWAYS COMPILE" flags
"000_Global/Geo" flags
"000_Global/Lighting" flags
"000_Global/Special Items" flags expanded
"000_Global/Special Items/Perk Machines" flags
"000_Global/Special Items/Pandora Boxes" flags
"000_Global/Special Items/Weapons" flags
"000_Global/Zones" flags
"000_Global/Zones/Zombie Zones" flags
"The Map" flags expanded
"_prefabs" flags prefab expanded
"_prefabs/zm/{map_name}/script/{map_name}_vending.map" flags prefab
"_prefabs/zm/{map_name}/script/{map_name}_magicboxes.map" flags prefab
"_prefabs/zm/{map_name}/script/{map_name}_weapons.map" flags prefab
// entity 0
{{
guid "{worldspawn_guid}"
"classname" "worldspawn"
"lightingquality" "1024"
"samplescale" "1"
"skyboxmodel" "skybox_default_day"
"ssi" "default_day"
"wsi" "default_day"
"fsi" "default"
"gravity" "800"
"lutmaterial" "luts_t7_default"
"numOmniShadowSlices" "24"
"numSpotShadowSlices" "64"
"sky_intensity_factor0" "1"
"sky_intensity_factor1" "1"
"state_alias_1" "State 1"
"state_alias_2" "State 2"
"state_alias_3" "State 3"
"state_alias_4" "State 4"
}}
// entity 1
{{
guid "{info_player_guid}"
"classname" "info_player_start"
"angles" "0 0 0"
"origin" "0 0 32"
}}
// entity 2
{{
guid "{spawn_marker_guid}"
layer "000_Global/ALWAYS COMPILE"
"classname" "script_struct"
"angles" "0 0 0"
"origin" "0 0 32"
"targetname" "initial_spawn_points"
"script_noteworthy" "player_1"
"_color" "1 0 0"
}}
// entity 3
{{
guid "{vending_ref_guid}"
layer "000_Global/Special Items/Perk Machines"
"classname" "misc_prefab"
"angles" "0 0 0"
"origin" "0 0 0"
"model" "_prefabs/zm/{map_name}/script/{map_name}_vending.map"
}}
// entity 4
{{
guid "{box_ref_guid}"
layer "000_Global/Special Items/Pandora Boxes"
"classname" "misc_prefab"
"angles" "0 0 0"
"origin" "0 0 0"
"model" "_prefabs/zm/{map_name}/script/{map_name}_magicboxes.map"
}}
// entity 5
{{
guid "{weapons_ref_guid}"
layer "000_Global/Special Items/Weapons"
"classname" "misc_prefab"
"angles" "0 0 0"
"origin" "0 0 0"
"model" "_prefabs/zm/{map_name}/script/{map_name}_weapons.map"
}}
"""


def _empty_prefab_template() -> str:
    """An empty prefab is just a worldspawn — no entities, no brushes."""
    guid = mapfile.new_guid()
    return f"""iwmap 4
"script_startingnumber" 0
"000_Global" flags  active
"The Map" flags expanded
// entity 0
{{
guid "{guid}"
"classname" "worldspawn"
"fsi" "default"
"gravity" "800"
"sky_intensity_factor0" "1"
"sky_intensity_factor1" "1"
"state_alias_1" "State 1"
"state_alias_2" "State 2"
"state_alias_3" "State 3"
"state_alias_4" "State 4"
}}
"""


def _gsc_template(map_name: str) -> str:
    """Minimal GSC main() that calls zm_usermap::main() and registers a single
    starter zone. Pattern follows zm_giant.gsc + the template_test_zone_init
    example at zm_usermap.gsc:159."""
    return f"""#using scripts\\codescripts\\struct;

#using scripts\\shared\\callbacks_shared;
#using scripts\\shared\\flag_shared;
#using scripts\\shared\\util_shared;

#insert scripts\\shared\\shared.gsh;
#insert scripts\\shared\\version.gsh;
#insert scripts\\zm\\_zm_utility.gsh;

#using scripts\\zm\\_load;
#using scripts\\zm\\_zm;
#using scripts\\zm\\_zm_zonemgr;
#using scripts\\zm\\_zm_utility;

// Perks (uncomment the ones you place via add_perk):
// #using scripts\\zm\\_zm_pack_a_punch;
// #using scripts\\zm\\_zm_perk_juggernaut;
// #using scripts\\zm\\_zm_perk_quick_revive;
// #using scripts\\zm\\_zm_perk_sleight_of_hand;
// #using scripts\\zm\\_zm_perk_doubletap2;
// #using scripts\\zm\\_zm_perk_deadshot;
// #using scripts\\zm\\_zm_perk_staminup;
// #using scripts\\zm\\_zm_perk_additionalprimaryweapon;

#using scripts\\zm\\zm_usermap;


function main()
{{
	zm_usermap::main();

	level.default_game_mode = "zclassic";
	level.zone_manager_init_func = &{map_name}_zone_init;

	// Zones are populated by add_zombie_zone (from the MCP) — first call
	// becomes init_zones[0] and gets set as default_start_location if you
	// passed is_starting_zone=true.
	init_zones = [];
	level thread zm_zonemgr::manage_zones( init_zones );
}}


function {map_name}_zone_init()
{{
	level flag::init( "always_on" );
	level flag::set( "always_on" );
}}
"""


def _csc_template(map_name: str) -> str:
    """Minimal client-side script for a zombies usermap.

    Crucially, this calls `zm_usermap::main()` from `main()` so the framework's
    CSC (`scripts\\zm\\zm_usermap.csc`) gets to run — that's what registers
    all the standard zombies clientfields (perks, powerups, weapons, traps,
    AI). Without this, the server GSC and client CSC disagree on the
    clientfield registry and BO3 disconnects at map join with `ERROR: Server
    Disconnected - Clientfield Mismatch`."""
    return f"""#using scripts\\codescripts\\struct;
#using scripts\\shared\\callbacks_shared;
#using scripts\\shared\\clientfield_shared;
#using scripts\\shared\\system_shared;
#using scripts\\shared\\util_shared;

#insert scripts\\shared\\shared.gsh;
#insert scripts\\shared\\version.gsh;

#using scripts\\zm\\zm_usermap;


REGISTER_SYSTEM( "{map_name}", &__init__, undefined )


function __init__()
{{
	callback::on_localplayer_spawned( &on_player_spawned );
}}


function main()
{{
	zm_usermap::main();
}}


function on_player_spawned( localClientNum )
{{
}}
"""


def _zone_manifest_template(map_name: str) -> str:
    return f""">class,zm_mod_level
>group,modtools
>title,{map_name}

>level.forced_model_lods,-4
>level.force_static_models,1
>level.force_view_models,1
>level.force_world_materials,1

// BSP
col_map,maps/zm/{map_name}.d3dbsp
gfx_map,maps/zm/{map_name}.d3dbsp

// Server scripts
scriptparsetree,scripts/zm/{map_name}.gsc

// Client scripts
scriptparsetree,scripts/zm/{map_name}.csc
"""
