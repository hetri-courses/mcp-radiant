"""Build pipeline: shells out to gdtdb / cod2map64 / linker_modtools with the
exact arguments the Mod Tools Launcher uses (captured 2026-04-30 by running a
real zm_giant build through the launcher and reading the Output Window)."""

from __future__ import annotations

import re
import subprocess
from collections import Counter

from . import paths

# CoD console color codes — strip when surfacing to user.
_COLOR_RE = re.compile(r"\^\d")
# Per-asset warning lines look like:
#   ^3DROPPED VERTS. N warnings encountered while processing xmodel '<asset>'. Log can be found here: <path>
_PER_ASSET_WARN_RE = re.compile(
    r"\^3.*?warnings? encountered while processing xmodel '([^']+)'", re.IGNORECASE
)
_GENERIC_WARN_RE = re.compile(r"^\^?3?\s*WARNING:\s*(.+?)$", re.MULTILINE)
_ERROR_RE = re.compile(r"^\^?1?\s*(?:UNRECOVERABLE )?ERROR:\s*(.+?)$", re.MULTILINE)
_LINKER_SUMMARY_RE = re.compile(
    r"There were (\d+) errors? and (\d+) warnings?", re.IGNORECASE
)


def _run(args: list[str], *, timeout: int = 600) -> dict:
    """Run a subprocess, capture output, return a structured result.
    Default timeout is 10 minutes — enough for a from-scratch link of a
    large map (zm_giant first-time was ~9 min)."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            env=paths.build_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": args,
            "returncode": -1,
            "timed_out": True,
            "timeout_seconds": timeout,
            "partial_output": (exc.stdout or "") + (exc.stderr or ""),
        }
    output = (proc.stdout or "") + (proc.stderr or "")
    return {
        "command": args,
        "returncode": proc.returncode,
        "timed_out": False,
        "output": output,
    }


def gdtdb_update() -> dict:
    """Stage 0: refresh the GDT asset database."""
    result = _run([str(paths.gdtdb()), "/update"], timeout=120)
    if not result["timed_out"]:
        result["summary"] = parse_warnings(result["output"])
    return result


def compile_map(map_name: str, *, only_ents: bool = True) -> dict:
    """Stage 1: cod2map64 .map -> .d3dbsp.

    `only_ents=True` (default) is the iteration path — entity-only changes
    compile in seconds. Set False when geometry, lighting, or per-map prefab
    changes need to take effect."""
    args = [
        str(paths.cod2map()),
        "-platform", "pc",
    ]
    if only_ents:
        args.append("-onlyents")
    args.extend([
        "-loadFrom", str(paths.map_source(map_name)),
        str(paths.bsp_output(map_name)),
    ])
    result = _run(args, timeout=600)
    if not result["timed_out"]:
        result["summary"] = parse_warnings(result["output"])
    return result


def link(map_name: str, *, language: str = "english") -> dict:
    """Stage 2: linker_modtools — packs the BSP + assets listed in the zone
    manifest into `<map>.ff` and `en_<map>.ff`."""
    args = [
        str(paths.linker()),
        "-language", language,
        "-modsource", map_name,
    ]
    # First-time link of a large map can take ~10 min while it converts every
    # referenced xmesh + image. Cached re-links are seconds.
    result = _run(args, timeout=900)
    if not result["timed_out"]:
        result["summary"] = parse_warnings(result["output"])
    return result


def build(map_name: str, *, only_ents: bool = True) -> dict:
    """Run the full chain: gdtdb update -> compile -> link. Stops at the first
    non-zero exit code so you don't see a useless link error after a compile fail."""
    db_result = gdtdb_update()
    if db_result.get("returncode", 0) != 0 or db_result.get("timed_out"):
        return {"stage": "gdtdb_update", "stages": [db_result]}
    compile_result = compile_map(map_name, only_ents=only_ents)
    if compile_result.get("returncode", 0) != 0 or compile_result.get("timed_out"):
        return {"stage": "compile", "stages": [db_result, compile_result]}
    link_result = link(map_name)
    return {
        "stage": "complete" if link_result.get("returncode", 0) == 0 else "link",
        "stages": [db_result, compile_result, link_result],
    }


def parse_warnings(output: str) -> dict:
    """Strip color codes and extract structured info from build output."""
    cleaned = _COLOR_RE.sub("", output)

    # Per-asset warnings (xmodel processing). Dedupe by asset.
    per_asset = Counter(m.group(1) for m in _PER_ASSET_WARN_RE.finditer(output))

    # Generic warnings/errors with the WARNING:/ERROR: prefix.
    warnings = sorted({m.group(1).strip() for m in _GENERIC_WARN_RE.finditer(cleaned)})
    errors = sorted({m.group(1).strip() for m in _ERROR_RE.finditer(cleaned)})

    # cod2map64 leak detection — the engine prints a banner of asterisks
    # around "leaked" and writes a .lin leakfile. Surface this prominently
    # because compilation still succeeds (returncode 0) even with leaks,
    # but in-game performance and visibility are degraded.
    leaked = bool(re.search(r'\*+\s*leaked\s*\*+', cleaned, re.IGNORECASE))
    leakfile_match = re.search(
        r'WROTE BSP LEAKFILE:\s*(\S.+?\.lin)', cleaned, re.IGNORECASE
    )
    leak_info = None
    if leaked:
        leak_info = {
            "leaked": True,
            "leakfile": leakfile_match.group(1).strip() if leakfile_match else None,
            "fix": "Open the .map in Radiant: File > Load Error File > "
                   "General Error File. The leakfile traces a line from "
                   "inside to outside; follow it to find the unsealed seam.",
        }

    # Count the most common missing-material warnings — these show up
    # frequently when the texture catalog has stale names (a real-world v0.9
    # learning), so worth surfacing the unique materials for fast triage.
    missing_materials = sorted({
        m.group(1) for m in re.finditer(
            r"Material '([^']+)' is missing", cleaned
        )
    })

    # Final linker summary, if present.
    summary_match = _LINKER_SUMMARY_RE.search(cleaned)
    summary_counts = None
    if summary_match:
        summary_counts = {
            "errors": int(summary_match.group(1)),
            "warnings": int(summary_match.group(2)),
        }

    return {
        "errors": errors,
        "warnings": warnings,
        "missing_materials": missing_materials,
        "per_asset_warning_counts": dict(per_asset.most_common(20)),
        "linker_summary": summary_counts,
        "leak": leak_info,
    }
