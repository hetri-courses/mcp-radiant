"""GSC script editing — minimally invasive line insertions for `#using` import
management and zone registration. Doesn't try to be a full GSC parser; uses
line-level matching that preserves the rest of the file verbatim.

When the scaffolded template ships an import as a comment (e.g.
`// #using scripts\\zm\\_zm_perk_juggernaut;`), `ensure_import` uncomments it
in place rather than inserting a duplicate."""

from __future__ import annotations

import re
from pathlib import Path

# zm_core prefab name -> GSC module path. Keys match the prefab basenames in
# `zm.PERKS.values()` so add_perk / add_pack_a_punch can look up the right
# import directly from what they're about to place.
PREFAB_TO_IMPORT: dict[str, str] = {
    "vending_juggernaut_struct":              "scripts\\zm\\_zm_perk_juggernaut",
    "vending_sleight_struct":                 "scripts\\zm\\_zm_perk_sleight_of_hand",
    "vending_revive_struct":                  "scripts\\zm\\_zm_perk_quick_revive",
    "vending_doubletap_struct":               "scripts\\zm\\_zm_perk_doubletap2",
    "vending_deadshot_struct":                "scripts\\zm\\_zm_perk_deadshot",
    "vending_marathon_struct":                "scripts\\zm\\_zm_perk_staminup",
    "vending_additionalprimaryweapon_struct": "scripts\\zm\\_zm_perk_additionalprimaryweapon",
    "vending_weapon_upgrade_spawnable":       "scripts\\zm\\_zm_pack_a_punch",
    # vending_bgb_struct (gobblegum) is in zm_usermap.gsc already; no extra import.
}


def ensure_import(gsc_path: Path, module_path: str) -> dict:
    """Make sure `#using <module_path>;` exists in the GSC file.

    - If the line is already present uncommented: no-op.
    - If the line is present as a `// ` comment: uncomment in place.
    - If absent entirely: insert after the last existing `#using` line.

    Preserves line endings (CRLF or LF) and leaves the rest of the file
    untouched. Returns a dict describing what action was taken."""
    if not gsc_path.exists():
        return {"action": "skipped", "reason": f"GSC not found: {gsc_path}"}

    using_line = f"#using {module_path};"
    commented = f"// {using_line}"

    # Read without newline translation so we preserve CRLF/LF as-is.
    text = gsc_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n").strip()
        if stripped == using_line:
            return {"action": "already_present", "import": module_path}
        if stripped == commented:
            # Uncomment in place. Find and replace within the line so we
            # don't disturb leading whitespace or line ending.
            lines[i] = line.replace(commented, using_line, 1)
            gsc_path.write_text("".join(lines), encoding="utf-8", newline="")
            return {"action": "uncommented", "import": module_path}

    # Not present at all — find the last #using line and insert after it.
    last_using_idx = -1
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#using "):
            last_using_idx = i

    if last_using_idx == -1:
        # Defensive: no #using lines at all. Insert at top.
        lines.insert(0, f"{using_line}\n")
    else:
        ending = "\r\n" if lines[last_using_idx].endswith("\r\n") else "\n"
        lines.insert(last_using_idx + 1, f"{using_line}{ending}")

    gsc_path.write_text("".join(lines), encoding="utf-8", newline="")
    return {"action": "inserted", "import": module_path}


# --- Zone registration ----------------------------------------------------

# Match `init_zones[N] = "name";` (with optional whitespace).
_INIT_ZONES_RE = re.compile(
    r'^(\s*)init_zones\[(\d+)\]\s*=\s*"([^"]+)"\s*;\s*$'
)
# Match `level.default_start_location = "name";`
_DEFAULT_START_RE = re.compile(
    r'^(\s*)level\.default_start_location\s*=\s*"([^"]+)"\s*;\s*$'
)


def add_init_zone(gsc_path: Path, zone_name: str) -> dict:
    """Append an `init_zones[N] = "<zone_name>";` entry to the GSC's main()
    function. Idempotent — if the zone is already in init_zones, no-op.

    Insertion strategy:
      - If `init_zones[N] = "...";` lines already exist, insert after the
        last one with index = max(N) + 1.
      - Otherwise (empty array, just `init_zones = [];`), insert after the
        `init_zones = [];` line at index 0. This is the v1.0 scaffold case
        where the array starts empty and gets populated by add_zombie_zone."""
    if not gsc_path.exists():
        return {"action": "skipped", "reason": f"GSC not found: {gsc_path}"}

    text = gsc_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    last_init_idx = -1
    empty_decl_idx = -1
    max_n = -1
    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        match = _INIT_ZONES_RE.match(stripped)
        if match:
            indent, n_str, existing_name = match.groups()
            n = int(n_str)
            last_init_idx = i
            if n > max_n:
                max_n = n
            if existing_name == zone_name:
                return {"action": "already_present", "zone": zone_name, "index": n}
            continue
        # Look for the empty-array declaration `init_zones = [];`
        if empty_decl_idx == -1 and re.match(
            r'^\s*init_zones\s*=\s*\[\s*\]\s*;\s*$', stripped
        ):
            empty_decl_idx = i

    if last_init_idx >= 0:
        anchor = last_init_idx
        new_n = max_n + 1
    elif empty_decl_idx >= 0:
        anchor = empty_decl_idx
        new_n = 0
    else:
        return {
            "action": "skipped",
            "reason": "no `init_zones = [];` or `init_zones[N] = ...;` line "
                      "found in GSC — structure unexpected",
        }

    # Match indent and line ending of the anchor line
    anchor_line = lines[anchor]
    leading_ws = re.match(r'^(\s*)', anchor_line).group(1)
    indent = leading_ws if leading_ws else "\t"
    ending = "\r\n" if anchor_line.endswith("\r\n") else "\n"

    new_line = f'{indent}init_zones[{new_n}] = "{zone_name}";{ending}'
    lines.insert(anchor + 1, new_line)

    gsc_path.write_text("".join(lines), encoding="utf-8", newline="")
    return {"action": "inserted", "zone": zone_name, "index": new_n}


def set_default_start_location(gsc_path: Path, zone_name: str) -> dict:
    """Set `level.default_start_location = "<zone_name>";` in the GSC.

    Behavior:
      - If the assignment exists with the same value, no-op (already_present).
      - If it exists with a different value, replace.
      - If absent, insert before `level.zone_manager_init_func = ...;` (which
        the scaffold always emits). Falls back to inserting before the
        `init_zones = [];` line if zone_manager_init_func isn't found."""
    if not gsc_path.exists():
        return {"action": "skipped", "reason": f"GSC not found: {gsc_path}"}

    text = gsc_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Look for an existing assignment first
    for i, line in enumerate(lines):
        match = _DEFAULT_START_RE.match(line.rstrip("\r\n"))
        if not match:
            continue
        indent, existing = match.groups()
        if existing == zone_name:
            return {"action": "already_present", "zone": zone_name}
        ending = "\r\n" if line.endswith("\r\n") else "\n"
        lines[i] = f'{indent}level.default_start_location = "{zone_name}";{ending}'
        gsc_path.write_text("".join(lines), encoding="utf-8", newline="")
        return {"action": "replaced", "previous": existing, "zone": zone_name}

    # Absent — insert before zone_manager_init_func or init_zones = []
    insert_before = -1
    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if re.match(r'^\s*level\.zone_manager_init_func\s*=', stripped):
            insert_before = i
            break
    if insert_before == -1:
        for i, line in enumerate(lines):
            if re.match(r'^\s*init_zones\s*=\s*\[\s*\]\s*;\s*$',
                        line.rstrip("\r\n")):
                insert_before = i
                break
    if insert_before == -1:
        return {
            "action": "skipped",
            "reason": "no anchor point found (no zone_manager_init_func or "
                      "init_zones = [];) to insert default_start_location",
        }

    anchor_line = lines[insert_before]
    leading_ws = re.match(r'^(\s*)', anchor_line).group(1)
    indent = leading_ws if leading_ws else "\t"
    ending = "\r\n" if anchor_line.endswith("\r\n") else "\n"
    new_line = f'{indent}level.default_start_location = "{zone_name}";{ending}'
    lines.insert(insert_before, new_line)
    gsc_path.write_text("".join(lines), encoding="utf-8", newline="")
    return {"action": "inserted", "zone": zone_name}


# --- Zone graph (door → zone wire-up) -------------------------------------

# `zm_zonemgr::add_adjacent_zone( "A", "B", "flag" )` lines we synthesize.
# Match either-direction quoting and tolerant whitespace for idempotency.
_ADJ_ZONE_RE = re.compile(
    r'zm_zonemgr::add_adjacent_zone\s*\(\s*'
    r'"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)\s*;'
)


def add_adjacent_zone_call(
    gsc_path: Path,
    zone_a: str,
    zone_b: str,
    flag_name: str,
) -> dict:
    """Insert `zm_zonemgr::add_adjacent_zone("a", "b", "flag");` inside the
    map's `<map>_zone_init` function. Idempotent — recognizes any of the four
    permutations (a/b ordered either way × flag) as already present.

    The framework's zone manager activates the linked zone when `flag` is set
    (e.g. by a door's `script_flag`). Two-way by default — either zone can
    activate the other, which matches the most common 'door between rooms'
    use case."""
    if not gsc_path.exists():
        return {"action": "skipped", "reason": f"GSC not found: {gsc_path}"}

    text = gsc_path.read_text(encoding="utf-8")

    # Idempotency check: any matching permutation already there?
    for match in _ADJ_ZONE_RE.finditer(text):
        a, b, f = match.groups()
        if f == flag_name and {a, b} == {zone_a, zone_b}:
            return {
                "action": "already_present",
                "zone_a": a, "zone_b": b, "flag": f,
            }

    # Find the zone_init function and insert before its closing brace.
    func_match = re.search(
        r'(function\s+\w*zone_init\s*\(\s*\)\s*\n\s*\{)',
        text,
    )
    if func_match is None:
        return {
            "action": "skipped",
            "reason": "no `function <map>_zone_init() { ... }` found in GSC",
        }

    body_start = func_match.end()
    depth = 1
    pos = body_start
    while pos < len(text) and depth > 0:
        ch = text[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        pos += 1
    if depth != 0:
        return {
            "action": "skipped",
            "reason": "unbalanced braces parsing zone_init function body",
        }

    close_brace_pos = pos
    indent = "\t"  # match the scaffolded template
    new_call = (
        f'{indent}zm_zonemgr::add_adjacent_zone( '
        f'"{zone_a}", "{zone_b}", "{flag_name}" );\n'
    )
    new_text = text[:close_brace_pos] + new_call + text[close_brace_pos:]
    gsc_path.write_text(new_text, encoding="utf-8", newline="")
    return {
        "action": "inserted",
        "zone_a": zone_a,
        "zone_b": zone_b,
        "flag": flag_name,
    }


# --- Generic level-var setter ---------------------------------------------


def set_level_var(gsc_path: Path, var_name: str, value: str) -> dict:
    """Set `level.<var_name> = <value>;` in the GSC's main() function.

    `value` is inserted verbatim — quote strings yourself (`'"start_chest"'`),
    or pass numerics/booleans bare (`'true'`, `'1'`).

    Idempotent: same value = no-op, different value = replace, absent = insert
    before `level.zone_manager_init_func = ...;` (the canonical anchor in
    scaffolded maps), with a fallback to before `init_zones = [];`.

    Used by add_mystery_box to set `level.start_chest_name` and
    `level.enable_magic` so the magic-box framework actually spawns chests."""
    if not gsc_path.exists():
        return {"action": "skipped", "reason": f"GSC not found: {gsc_path}"}

    text = gsc_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    pattern = re.compile(
        rf'^(\s*)level\.{re.escape(var_name)}\s*=\s*(.+?)\s*;\s*$'
    )
    for i, line in enumerate(lines):
        match = pattern.match(line.rstrip("\r\n"))
        if not match:
            continue
        indent, existing = match.groups()
        if existing == value:
            return {"action": "already_present", "var": var_name, "value": value}
        ending = "\r\n" if line.endswith("\r\n") else "\n"
        lines[i] = f'{indent}level.{var_name} = {value};{ending}'
        gsc_path.write_text("".join(lines), encoding="utf-8", newline="")
        return {
            "action": "replaced", "var": var_name,
            "previous": existing, "value": value,
        }

    insert_before = -1
    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if re.match(r'^\s*level\.zone_manager_init_func\s*=', stripped):
            insert_before = i
            break
    if insert_before == -1:
        for i, line in enumerate(lines):
            if re.match(
                r'^\s*init_zones\s*=\s*\[\s*\]\s*;\s*$',
                line.rstrip("\r\n"),
            ):
                insert_before = i
                break
    if insert_before == -1:
        return {
            "action": "skipped",
            "reason": "no zone_manager_init_func or init_zones anchor found",
        }

    anchor_line = lines[insert_before]
    leading_ws = re.match(r'^(\s*)', anchor_line).group(1)
    indent = leading_ws if leading_ws else "\t"
    ending = "\r\n" if anchor_line.endswith("\r\n") else "\n"
    new_line = f'{indent}level.{var_name} = {value};{ending}'
    lines.insert(insert_before, new_line)
    gsc_path.write_text("".join(lines), encoding="utf-8", newline="")
    return {"action": "inserted", "var": var_name, "value": value}
