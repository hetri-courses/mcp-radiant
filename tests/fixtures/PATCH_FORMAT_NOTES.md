# BO3 iwmap 4 patch/mesh format — archaeology notes

This is the format Treyarch's Mod Tools (Radiant) uses for curved
patches and triangulated meshes in `.map` source files. Derived from
shipping `.map` files in
`D:/Steam/steamapps/common/Call of Duty Black Ops III 455130/map_source/`.

## TL;DR

- Patches live INSIDE the same `// brush N { ... }` containers as
  regular brushes. The mapfile parser doesn't need to learn a new
  primitive type — it preserves the brush block as opaque text and the
  emitter writes a different inner body.
- Two primitive keywords: `curve` (Bezier-smoothed) and `mesh`
  (triangulated, no curvature smoothing).
- For outdoor terrain (rocks/hills), Treyarch uses `mesh`, not `curve`.
  See `mp_sector_terrain_north_tunnel_rocks.map` — the canonical
  reference for "terrain as patch mesh."

## The canonical terrain pattern

Treyarch emits TWO `mesh` brushes per terrain chunk:

1. **Visual mesh** — no `contents` line (= default visible/renderable).
   Uses the surface material (e.g. `t7_rock_sand_crumbled_medium_golden`).

2. **Collision/clip mesh** — same control points, but with
   `contents weaponClip detail ai_nosight;`. Uses a "blend" variant
   texture. `ai_nosight` means zombies can't see through it (so they
   path TO the player, not AROUND the rock).

This dual-emit pattern is why "patches have no collision" is a myth —
patches DO collide when their `contents` flags include physical solidity.

## Brush block structure

```
// brush 0
 {
  guid "{GUID}"
  mesh                              # or `curve`
  {
  contents weaponClip detail ai_nosight;   # OPTIONAL; omit for renderable
  toolFlags;                        # OPTIONAL but Treyarch always emits
   <render_material>                # e.g. t7_rock_sand_crumbled_medium_golden
   <lightmap_material>              # almost always lightmap_gray
   <W> <H> <T1> <T2>                # see "dimension line"
   (
    v <X> <Y> <Z> [c R G B A] t <U> <V> <Lu> <Lv>   # one control point
    v ...
    ...                             # H control points per row
   )
   ( ... )                          # W rows total
   decalLayerSort "Sand"            # OPTIONAL decal sort tag
   decalEditorSort -1               # OPTIONAL
  }
 }
```

## Dimension line: `W H T1 T2`

From observed examples:
- `2 2 0 8`  — door_frame_metal_rusted (smallest)
- `5 3 16 6` — zoneb_glass_roof_arch_01 (curve, Bezier)
- `5 4 0 8`  — mp_sector terrain rock (mesh, flat-triangulated)
- `9 3 16 8` — burn_barrel (curve, Bezier)
- `9 3 16 1` — zm_giant rail trim (curve)

Inference:
- **W** = number of rows of control points (first index)
- **H** = number of control points per row
- **T1** = smoothing/subdivision factor for `curve` blocks (16 is
  standard, higher = smoother). For `mesh` it appears to be 0.
- **T2** = tessellation/subdivision count (related to lightmap
  density?). 8 is common; varies.

For terrain emitter: emit `mesh` with `T1=0 T2=8` (matches Treyarch
rocks). For decorative curves: `curve` with `T1=16 T2=8`.

## Vertex line: `v X Y Z [c R G B A] t U V Lu Lv`

- `X Y Z` — world-space position of the control point (float).
- `c R G B A` — OPTIONAL vertex color (0..255). Used on the collision
  mesh in rock terrain example for texture blending. Omit for plain
  terrain.
- `t U V Lu Lv` — texture coordinates.
  - `U V` are world-projected texture UVs (large numbers — looks like
    they're `world_position * texel_scale`).
  - `Lu Lv` are lightmap UVs (small numbers, ~1..130 range across a
    patch).

For a heightmap terrain emitter, simplest correct UV scheme:
- World-projected texture UV: U = X * uv_scale, V = Y * uv_scale,
  with uv_scale ≈ 8 (matches "texel_scale = 8 BO3 inches per texture
  pixel" — terrain texture wraps every ~64-unit cell). The negative
  sign on V in the examples is a Treyarch convention; matches the way
  axis-aligned brush face params use negative V.
- Lightmap UV: monotonic 1..(K*32+1) where K is the number of rows
  (or columns) of control points. Pattern from rock example:
  Lu=30, 30.5, 31, 32, 33 (one row apart = +0.5 to +1; ~1 unit per
  row). Lv=44.7, 45.5, 46.3, 47.8 (~1 unit per column).
- For our terrain emitter, simpler is fine: Lu/Lv normalized 1..K
  where K = number of CPs in that direction.

## Control point order

Within a row, control points proceed monotonically in ONE axis.
Across rows, they proceed in the OTHER axis. From mp_sector rock,
the X axis is roughly the column index and Y axis is the row index
(both vary, but the dominant variation per-row is in one axis).

For our terrain emitter:
- Row index `i` (0..W-1) = X direction
- Column index `j` (0..H-1) = Y direction
- CP[i][j] world position = (origin.x + i * cell_size,
                             origin.y + j * cell_size,
                             heightmap[i][j])

## Texture choices

Verified terrain textures from `mp_sector` rocks:
- `t7_rock_sand_crumbled_medium_golden`  — visual ground
- `t7_rock_sand_crumbled_large_golden_blend` — blend variant (used on
  collision mesh in stock prefab; safe fallback)

Other terrain-y textures (from list_textures + grepping stock maps):
- `t7_concrete_pebbles_cracked` — gravelly ground
- `t7_concrete_tiles_2x2_dirty_01` — tile floor
- `t7_zm_der_tile_hexagon` — ZM hex tile floor (matches user's
  "tiled floor with terrain breaking through" aesthetic)
- `t7_snow_powder_01` — snow cap

## Fixtures

- `patch_rock_visual.mapfrag` — first mesh from mp_sector rocks
- `patch_rock_collision.mapfrag` — collision twin
- `patch_door_frame_2x2.mapfrag` — smallest possible mesh (door frame)
- `patch_curve_glass_arch.mapfrag` — curve example for comparison

## Runtime status (v23.0 vs v23.1)

**v23.0 lessons that did not survive playtest:**

1. **Compile acceptance ≠ runtime correctness.** `cod2map64` printing
   `building curve/terrain collision...` proves the format parsed; it
   does NOT prove the patch renders, collides, or pathfinds correctly.
   Runtime playtest is the only verification that counts.

2. **DO NOT use `caulk` as the texture on a collision-twin patch.**
   `cod2map64` emits this warning at compile time:

       *****
       N terrain patches with caulk were discovered.
       Use 'File/Load Error File/General Error File' in Radiant to view.
       *****

   The build still succeeds (rc=0) but the resulting patches render as
   dark/black blobs in-game and the lighting bake doesn't process them
   correctly. Match Treyarch's stock pattern: BOTH the visual mesh and
   the collision twin use real material names (e.g.
   `t7_concrete_pebbles_cracked` on both, with the contents flag
   determining behavior). The collision twin is rendered but barely
   visible because BSP folds it onto the visual mesh.

3. **`ai_nosight` is for OBSTACLES, not floors.** The stock pattern
   `weaponClip detail ai_nosight` is correct for rock terrain (zombies
   path AROUND the rock, not through it). For walkable FLOOR terrain,
   `ai_nosight` blocks zombies from seeing across the patch, breaking
   their pathing. For floor patches use `weaponClip detail` only — no
   `ai_nosight`.

4. **Player spawn must NOT overlap a patch footprint.** In zm_patch_format_test
   the patch covered x=[-400..-144] y=[-128..128], and player spawn at
   (-320, 0, 32) was inside that XY footprint. The patch surface at
   (-320, 0) was z≈40, above the spawn z=32 — the player was pushed up
   onto the patch at spawn, producing the "weirdly elevated" feeling.
   Player spawns must be on plain floor brushes well outside any patch
   footprint.

5. **Don't test patches in a map with delicate barricade/riser logic.**
   The starter-room barricade+riser+courtyard pattern has zero margin
   for new geometry interfering with zombie pathing. Patch tests belong
   in their own isolated maps (`zm_patch_lab_NN`).

**v23.1 verified-OK pattern (zm_patch_lab_02 build):**

- Visual mesh: real material (e.g. `t7_concrete_pebbles_cracked`),
  no `contents` line.
- Collision twin (optional, only if you need solid floor collision):
  SAME real material, `contents weaponClip detail` (no ai_nosight).
- Player spawn: on a plain flat floor brush, far from patches.

Compile output: NO "terrain patches with caulk" warning, no errors,
leak null, navmesh generated.

**v23.2 fix: control-point axis convention.** lab_02 runtime playtest
revealed:
- Collision WORKS (player walked up the slopes as expected).
- Patches were INVISIBLE from above, only visible from underneath at
  grazing angles. The surface normal was pointing DOWN — patches were
  back-face culled.

Cause: the control-point grid axis order. Stock Treyarch
(`mp_sector_terrain_north_tunnel_rocks.map`) uses:
- **outer index (W in `W H T1 T2` dim line) → +X axis**
- **inner index (H) → +Y axis**

This convention gives +Z-facing surface normals. The v23.0/v23.1
emitter had it backward (outer→Y, inner→X), producing -Z normals.

Verified by parsing stock rock prefab CPs:
- row 0 col 0 = (-747, 2483, 368)
- row 1 col 0 = (-631, 2475, 368)  — outer step: dx≈+116, dy≈-8 (X-dominant)
- row 0 col 1 = (-684, 2515, 389)  — inner step: dx≈+63, dy≈+32 (Y-dominant)

**Fix in `patches.py` v23.2:**
- `mesh_block` docstring now explicitly documents the convention.
- `heightmap_to_mesh_patches` reorders internally: it accepts
  `heightmap[y_idx][x_idx]` (natural reading order) but emits
  `cps[outer_x][inner_y]` so the engine sees +Z normals.

When emitting a patch by hand via `mesh_block`, build your
`control_points` as `cps[x_index][y_index]` — the OUTER loop sweeps X.

If a patch is invisible from above but visible from below, the axis
order is wrong.

**v23.4 (zm_patch_ai_lab_01 → ai_lab_02): two more bugs caught**

ai_lab_01 runtime had TWO regressions vs lab_03:

1. **Patch invisible again.** lab_03 worked because its CPs happened to
   sweep +X in the outer index. ai_lab_01's ramp helper made outer X
   *decrease* (128 → -64), which flipped the normal direction. The rule
   isn't just "outer = X axis" — it's "outer must INCREASE in +X."

   v23.4 fix: `mesh_block` now has `auto_orient=True` (default). It
   inspects the first outer/inner steps and:
   - transposes if outer is Y-dominant
   - reverses the outer order if outer step is -X
   - reverses each row if inner step is -Y
   so the emitted mesh ALWAYS has +Z normals regardless of how the
   caller built the CP grid. Set `auto_orient=False` only for ceiling
   patches where you want a downward normal.

2. **No zombies spawned.** `add_barricaded_starter_room` adds the
   barricade prefabs + exterior riser script_structs but does NOT
   add an `actor_spawner_zm_factory_zombie` — that's the AI template
   the framework uses to instantiate zombies. Without one, the spawn
   structs are dangling targets with no source factory.

   In `make_playable_zombie_foundation` this is handled by the
   subsequent `furnish_zone` call. In a standalone test map you must
   call `add_zombie_spawner(..., zone_name=...)` explicitly somewhere
   in the zone (the origin doesn't need to be visible — it's just
   the AI template).
- Slopes render correctly from above with their declared materials
- Player walks up `weaponClip detail` slopes smoothly
- `contents=None` slopes have no collision (player walks through)
- The umbra/sky-cull bug from on-top-of-patch in v23.1 also resolved
  (the back-facing normals were polluting the visibility cluster)
- Patches are 1-sided: underside is open. For a "solid"-looking ramp
  you'd need box brushes underneath. For TD terrain on flat ground,
  the underlying floor brush hides the gap.

## Voxel terrain reference (for fallback mode)

`zm_terrain_test.map` is Treyarch's "terrain test" — and it's pure
voxel box brushes, not patches. 1489 brushes, 128×128 unit cells,
heights 80-130. Textures: `t7_concrete_wall_dark_01` for cliff faces,
`t7_concrete_pebbles_cracked` for ground tops.

So voxel terrain IS a valid Treyarch pattern — it's just used for
"rocky terrain" aesthetic (cliffs, mesas) rather than smooth hills.
The blockiness in our v22.14 maps isn't a "wrong rendering choice" —
it's the voxel terrain aesthetic. But the user wants smooth terrain
for outdoor environments, which means `mesh` patches.
