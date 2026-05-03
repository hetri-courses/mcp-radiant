"""Curated catalog of BO3 texture/material names.

**These are ground-truth.** Every entry was extracted from actual brush face
lines in the shipping `zm_giant` map source (top-level `.map`, geo prefabs,
and the `zm_core` shared library) — meaning every name has compiled cleanly
in Treyarch's own build. Trust over comprehensiveness: there are more
materials in the asset DB, but these are the ones provably available.

If you reference a material that isn't here and `cod2map64` warns
"Material 'X' is missing", the surface will appear as the engine's default
error texture in-game. Use `list_textures()` from chat to see what's safe.

Decals (`DECALS`) and chalk-buy outlines (`CHALK_BUY`) are used as the
`modeloverridematerial` KVP on `misc_volume_decal` entities, not as brush
face textures, so they're listed separately and validated differently.
"""

from __future__ import annotations

# Wall/floor/ceiling materials, grouped by surface type. Many BO3 materials
# come in `_wet` (weather-rain-aware) and `_nw` (no-weather, dry) variants —
# both forms are listed where they exist in zm_giant.
WALLS = {
    "concrete": [
        "t7_concrete_trowelled",
        "t7_concrete_wall_dark_01",
        "t7_concrete_wall_blocks_wet",
        "t7_concrete_wall_poured_thick_01_wet",
        "t7_concrete_wall_weathered_01_wet",
        "t7_concrete_pebbles_cracked",
        "t7_concrete_bare_dark_01_wet",
        "t7_concrete_bare_weathered_01_wet",
        "t7_concrete_poured_bunker_dirty_01_wet",
        "t7_concrete_poured_bunker_paint_01_blue_lt",
        "t7_concrete_poured_bunker_paint_01_grey_lt",
    ],
    "brick": [
        "t7_brick_worn_heavy_grout_red",
        "t7_brick_worn_heavy_grout_brown_wet",
        "t7_brick_worn_dirty_red_wet",
        "t7_brick_trim_worn_dirty_red",
    ],
    "wood": [
        "t7_wood_planks_rustic",
        "t7_wood_planks_worn",
        "t7_wood_planks_damaged_teak",
        "t7_wood_worn_dark_oak",
        "t7_wood_worn_brown",
        "t7_wood_particle_worn",
        "t7_wood_plywood_bare",
        "t7_wood_beam_worn_oak",
        "t7_wood_lath_plaster_01",
        "jun_art_wood_plywood_dark03",  # used by zm_core/buyable_magic_box_start
    ],
    "plaster": [
        "t7_plaster_cracked_light_01",
        "t7_plaster_rough_worn_01_wet",
        "t7_plaster_smooth_dirty_01_blue_pale_wet",
    ],
    "metal": [
        "t7_metal_corrugated_01",
        "t7_metal_corrugated_rust",
        "t7_metal_diamond_plate_panel_worn_wet",
        "t7_metal_diamond_plate_worn_wet",
        "t7_metal_panel_2x1_iron_polished",
        "t7_metal_panel_4x4_tungsten_polished",
        "t7_metal_panels_painted_2x1_grey",
        "t7_metal_paint_rust_brown",
        "t7_metal_paint_worn_grey",
        "t7_metal_rust_ceiling_01",
        "t7_metal_aged_ceiling_01",
        "t7_metal_bare_tungsten_brushed_2_worn",
        "t7_metal_garage_door_rollup_01_bare_wet",
        "t7_metal_graphite_matte_black",
        "t7_metal_grunge_light_01",
        "t7_metal_grunge_med_01",
        "t7_metal_wall_panel_old",
        "t7_metal_worn_iron_dark",
    ],
    "glass": [
        "t7_glass_dirty_streaks",
        "t7_glass_plain_cracked",
        "t7_glass_plain_opaque",
        "t7_glass_window_warehouse",
        "t7_glass_window_warehouse_backlit",
    ],
    "tile": [
        "t7_zm_der_tile_hexagon",
        "t7_zm_der_tile_hexagon_nw",
        "t7_ceramic_tile_shower_red",
        "t7_ceramic_tile_shower_white_cracks",
        "t7_ceramic_tiles_16x16",
        "t7_ceramic_tiles_6x6_dirty_tan",
    ],
    "snow": [
        "t7_snow_powder_01",
        "t7_snow_powder_02",
        "t7_snow_powder_footprints_01",
        "t7_glass_snow_buildup_01_backlit",
    ],
}

# Materials suitable for floor surfaces (overlap with WALLS but worth listing
# separately for ergonomics — these are what zm_giant actually walks on).
FLOORS = [
    "t7_concrete_floor_garage_cracked_wet_nw",
    "t7_concrete_pebbles_cracked",
    "t7_concrete_tiles_2x2_dirty_01",
    "t7_concrete_tiles_2x2_dirty_01_wet",
    "t7_asphalt_damaged_dark_wet_nw",
    "t7_zm_der_tile_hexagon",
    "t7_ceramic_tiles_16x16",
    "t7_ceramic_tiles_6x6_dirty_tan",
    "t7_metal_floor_catwalk_reinforced",
    "t7_metal_diamond_plate_panel_worn_wet",
    "t7_wood_planks_worn",
]

# Trim / accent strips.
TRIM = [
    "t7_concrete_floor_trim_01",
    "t7_concrete_trim_beveled_01_wet",
    "t7_concrete_trim_beveled_03",
    "t7_concrete_trim_edge_damaged_01",
    "t7_wood_trim_brown_01",
    "t7_wood_trim_dark_brown",
    "t7_metal_aged_trim_beam_01",
    "t7_metal_grate_trim_01",
    "t7_metal_planter_trim",
    "t7_metal_planter_top_trim",
    "t7_metal_trim_plasma_cut_01",
    "t7_metal_trim_rivets_01",
    "t7_metal_trim_rivets_02",
    "t7_metal_trim_worn_iron_dark",
]

# Non-rendering / collision-only / engine-special. The engine recognizes
# these by name and applies the corresponding behavior at compile time.
SPECIAL = {
    "caulk":             "Non-rendering. Use on hidden brush faces — surface "
                         "is stripped during BSP.",
    "caulk_transparent": "Like caulk, but doesn't block player line-of-sight "
                         "(useful behind glass).",
    "caulk_sun_shadow":  "Caulk that still casts sun shadows (rare).",
    "nodraw_decal":      "Non-rendering surface that still accepts decals.",

    # Collision variants — invisible but block specific things
    "clip":              "Generic collision-only. Player + bullets blocked.",
    "clip_ai":           "Blocks AI navigation only.",
    "clip_full":         "Blocks everything (player, AI, bullets, missiles).",
    "clip_missile":      "Blocks projectiles; player passes through.",
    "clip_missile_no_player": "Same as clip_missile, explicitly NOT player.",
    "clip_nosight":      "Blocks player + bullets + AI sight lines.",
    "clip_player":       "Blocks player only; AI/bullets pass.",
    "clip_slick":        "Player slides on this surface.",
    "clip_weapon":       "Blocks bullets only.",
    "clip_weap_glass":   "Bullet-block for breakable glass.",
    "concrete_clip":     "Concrete-flavored clip (footstep sound).",
    "dirt_clip_nosight": "Dirt-flavored clip with sight blocking.",
    "metal_clip":        "Metal-flavored clip (for clean metal collision).",
    "metal_clip_catwalk":"Metal clip used on catwalks (footstep sound + collision).",
    "metal_clip_full":   "Full metal collision block.",
    "metal_clip_nosight":"Metal clip with sight blocking.",
    "wood_clip_nosight": "Wood-flavored clip with sight blocking.",

    # Triggers and special volumes
    "trigger":           "Marks a brush as a trigger volume (used inside "
                         "trigger_use, trigger_multiple, etc.). Invisible.",
    "sound_trigger":     "Trigger volume specialized for audio events.",

    # Compiler / engine markers (apply behaviors based on the brush volume)
    "volume":            "Generic volume marker.",
    "volume_fpstool":    "FPS-tool volume (used by mod tools — appears in "
                         "Special Items/Teleporter layers in zm_giant).",
    "exposure_volume":   "Auto-exposure region.",
    "fog":               "Fog volume.",
    "litfog_volume":     "Lit-fog volume (fog with light interaction).",
    "outdoorbounds_volume": "Marks the outdoor playable region (for streaming).",
    "sun_volume":        "Volume defining sun behavior in a region.",
    "umbra_volume":      "Umbra (occlusion culling) computation volume.",
    "umbra_high_lod":    "Umbra brush flagged as high-detail.",
    "umbra_small_occluder": "Umbra hint for small occluders.",
    "vista_volume":      "Background-vista region (skybox-style distant geo).",
    "weathergrime_volume":"Region where weather grime/wetness accumulates.",
    "shadowcaster":      "Force a brush to cast shadows (cosmetic only).",
    "sky":               "Sky brush — renders the skybox texture.",
    "traverse":          "AI traversal hint volume.",
    "global_black":      "Pure black material (inside-of-skybox / dark voids).",
}

# Wall-buy chalk gun outlines — used as `modeloverridematerial` on a
# misc_volume_decal next to a wall-buy. Pass these to `add_chalk_decal(material=...)`.
CHALK_BUY = [
    "t7_zm_chalk_buy_arak",
    "t7_zm_chalk_buy_bowie",
    "t7_zm_chalk_buy_cqw",
    "t7_zm_chalk_buy_frag",
    "t7_zm_chalk_buy_hvk30",
    "t7_zm_chalk_buy_krm",
    "t7_zm_chalk_buy_kuda",
    "t7_zm_chalk_buy_m8a4",
    "t7_zm_chalk_buy_shiva",
    "t7_zm_chalk_buy_spyder",
    "t7_zm_chalk_buy_trip_mine",
    "t7_zm_chalk_buy_triton",
    "t7_zm_chalk_buy_vmp",
]

# Decals projected via misc_volume_decal. These were inferred from build-time
# image references in zm_giant — not as well-vetted as brush-face materials.
# If a decal name fails, search the asset DB for the exact material asset.
DECALS = {
    "blood": [
        "t7_decal_blood_splatter_02",
        "t7_decal_blood_splatter_03",
        "t7_decal_blood_smear_01",
        "t7_decal_blood_smear_02",
        "t7_decal_blood_drips_02",
        "t7_decal_blood_drips_03",
        "t7_decal_blood_pool_01",
        "t7_decal_blood_grunge_01",
        "t7_decal_blood_hand_01",
        "t7_decal_blood_hand_02",
        "t7_decal_blood_droplets_01",
        "t7_decal_blood_droplets_02",
        "t7_decal_blood_spray_01",
        "t7_decal_blood_spray_02",
    ],
    "grunge": [
        "t7_decal_grunge_water_stain_01",
        "t7_decal_grunge_water_stain_04",
        "t7_decal_grunge_water_puddle_blend",
        "t7_decal_grunge_oil_stain_01",
        "t7_decal_grunge_oil_stain_02",
        "t7_decal_grunge_oil_stain_wet_03",
        "t7_decal_grunge_burnt_stain_01",
        "t7_decal_grunge_burnt_stain_02",
        "t7_decal_grunge_charred_stain_01",
        "t7_decal_grunge_charred_stain_02",
        "t7_decal_grunge_rust_02",
        "t7_decal_grunge_rust_05",
        "t7_decal_grunge_rust_07",
        "t7_decal_grunge_papers_01",
        "t7_decal_grunge_papers_02",
        "t7_decal_grunge_pool_silt_01",
        "t7_decal_grunge_leaking_03",
        "t7_decal_grunge_leaking_04",
        "t7_decal_grunge_dirty_tile_01",
        "t7_decal_grunge_wall_scratch_marks",
    ],
    "damage": [
        # These are brush-face surfaces in zm_giant (extracted as ground truth):
        "t7_decal_damage_concrete_tile_01",
        "t7_decal_damage_plaster_drywall_01",
    ],
    "snow": [
        # Snow decals — surfaces that "paint" snow onto walls/floors.
        "t7_decal_snow_powder_01",
        "t7_decal_snow_powder_02",
        "t7_decal_snow_bricks_heavy_grout_01",
        "t7_decal_snow_bricks_worn_01",
    ],
}


def list_all() -> dict:
    """Return the full catalog organized by category."""
    return {
        "walls": WALLS,
        "floors": FLOORS,
        "trim": TRIM,
        "special": SPECIAL,
        "chalk_buy": CHALK_BUY,
        "decals": DECALS,
    }


def list_category(category: str) -> dict | list:
    """Return one category's entries. Raises KeyError if unknown."""
    catalog = list_all()
    key = category.lower()
    if key not in catalog:
        raise KeyError(
            f"unknown texture category {category!r}. "
            f"Available: {sorted(catalog)}"
        )
    return catalog[key]


# Flat set of all known brush-face materials (walls/floors/trim/special),
# for fast membership checking. Decals/chalk_buy aren't here because they
# apply via the modeloverridematerial KVP, not on brush faces.
ALL_BRUSH_MATERIALS: set[str] = (
    {m for sublist in WALLS.values() for m in sublist}
    | set(FLOORS)
    | set(TRIM)
    | set(SPECIAL)
)


def is_known_brush_material(name: str) -> bool:
    """Return True if `name` is a vetted brush-face material from zm_giant."""
    return name in ALL_BRUSH_MATERIALS
