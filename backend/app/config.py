"""Process-wide configuration, read once from the environment.

Phanero Labs runs natively on the lab host (systemd) or in a container; all
paths default to directories inside the repo checkout so a native install
needs no extra env, while a container sets them explicitly.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name) or default).resolve()


LAB_STATE_DIR: Path = _path("LAB_STATE_DIR", REPO_ROOT / "lab_state")
DEVICE_IMAGES_DIR: Path = _path("DEVICE_IMAGES_DIR", REPO_ROOT / "device_images")
CURRICULUM_DIR: Path = _path("PLABS_CURRICULUM_DIR", REPO_ROOT / "curriculum")

# Optional modules
AI_ENABLED = _flag("PLABS_AI_ENABLED")
LAB_TELEMETRY_ENABLED = _flag("PLABS_LAB_TELEMETRY_ENABLED")
# Learning add-on: lessons, modules (and later curricula). Off → their API
# answers 404, nothing is seeded, and lesson labs behave as ordinary labs.
LEARNING_ENABLED = _flag("PLABS_LEARNING_ENABLED")
