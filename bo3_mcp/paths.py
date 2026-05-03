from __future__ import annotations

import os
from pathlib import Path

MOD_TOOLS_ROOT = Path(
    os.environ.get(
        "BO3_MOD_TOOLS",
        r"D:\Steam\steamapps\common\Call of Duty Black Ops III 455130",
    )
)


def root() -> Path:
    if not MOD_TOOLS_ROOT.exists():
        raise RuntimeError(
            f"BO3 mod tools not found at {MOD_TOOLS_ROOT}. "
            "Set the BO3_MOD_TOOLS env var to override."
        )
    return MOD_TOOLS_ROOT


def map_source(map_name: str) -> Path:
    return root() / "map_source" / "zm" / f"{map_name}.map"


def map_prefab_dir(map_name: str) -> Path:
    return root() / "map_source" / "_prefabs" / "zm" / map_name


def core_prefab(prefab_name: str) -> Path:
    name = prefab_name if prefab_name.endswith(".map") else f"{prefab_name}.map"
    return root() / "map_source" / "_prefabs" / "zm" / "zm_core" / name


def core_prefab_ref(prefab_name: str) -> str:
    """Path string used inside .map files for misc_prefab model KVPs."""
    name = prefab_name if prefab_name.endswith(".map") else f"{prefab_name}.map"
    return f"_prefabs/zm/zm_core/{name}"


def gsc(map_name: str) -> Path:
    return root() / "share" / "raw" / "scripts" / "zm" / f"{map_name}.gsc"


def csc(map_name: str) -> Path:
    return root() / "share" / "raw" / "scripts" / "zm" / f"{map_name}.csc"


def usermap_dir(map_name: str) -> Path:
    return root() / "usermaps" / map_name


def zone_manifest(map_name: str) -> Path:
    return usermap_dir(map_name) / "zone_source" / f"{map_name}.zone"


def bsp_output(map_name: str) -> Path:
    return root() / "share" / "raw" / "maps" / "zm" / f"{map_name}.d3dbsp"


def bin_dir() -> Path:
    return root() / "bin"


def cod2map() -> Path:
    return bin_dir() / "cod2map64.exe"


def linker() -> Path:
    return bin_dir() / "linker_modtools.exe"


def gdtdb() -> Path:
    return root() / "gdtdb" / "gdtdb.exe"


def build_env() -> dict[str, str]:
    """Env vars the build chain expects (set by modtools_setenv.bat)."""
    r = root()
    env = os.environ.copy()
    env["TA_GAME_PATH"] = f"{r}\\"
    env["TA_LOCAL_ASSET_CACHE"] = f"{r / 'share' / 'assetconvert'}\\"
    env["TA_TOOLS_PATH"] = f"{r}\\"
    return env
