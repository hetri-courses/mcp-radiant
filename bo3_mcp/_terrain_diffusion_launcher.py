"""Bootstrap wrapper around `terrain_diffusion.inference.api`.

Why this exists: `torch-directml` registers the `privateuseone` backend
lazily, only when you `import torch_directml`. If something else does
`tensor.to("privateuseone:0")` first, PyTorch tries to dispatch via a
`torch.privateuseone` module that doesn't exist and you get:

    ModuleNotFoundError: No module named 'torch.privateuseone'

The terrain-diffusion API code accepts an arbitrary `--device` string and
hands it straight to `pipeline.to(device)`, so we have to pre-import
`torch_directml` ourselves before launching the API. That's all this
wrapper does — sniff sys.argv / env, conditionally import torch_directml,
then hand off to the real `click` entrypoint.

This script is invoked by `bo3_mcp.terrain.start_terrain_diffusion_server`
and runs inside the terrain-diffusion venv's Python — NOT the MCP's
Python. It doesn't import anything from `bo3_mcp` itself; only stdlib +
torch_directml + terrain_diffusion (all installed in the venv).
"""
from __future__ import annotations

import os
import sys


def _requested_device() -> str:
    """Inspect TERRAIN_DEVICE env var and --device flag in argv; return
    whichever is set (env wins, then flag, else empty string)."""
    dev = os.environ.get("TERRAIN_DEVICE", "")
    if not dev and "--device" in sys.argv:
        idx = sys.argv.index("--device")
        if idx + 1 < len(sys.argv):
            dev = sys.argv[idx + 1]
    return dev


def main() -> None:
    device = _requested_device()
    if device.startswith("privateuseone"):
        # Pre-import torch_directml to register the backend. Without this,
        # the first `tensor.to('privateuseone:0')` inside the api code
        # raises ModuleNotFoundError.
        try:
            import torch_directml  # noqa: F401
        except ImportError as e:
            print(
                f"WARNING: --device={device!r} requested but torch_directml "
                f"could not be imported ({e}). Falling back to whatever "
                f"terrain-diffusion auto-selects.",
                file=sys.stderr,
            )

    # Hand off to terrain_diffusion's own click entrypoint. It will read
    # sys.argv[1:] for the model path / --port / --device / etc.
    from terrain_diffusion.inference.api import main as _td_main
    _td_main()


if __name__ == "__main__":
    main()
