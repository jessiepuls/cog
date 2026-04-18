import os
import re
from pathlib import Path


def project_slug(project_dir: Path) -> str:
    """lowercase basename; non-[a-z0-9_.-] replaced with '-'. Empty → 'project'."""
    name = project_dir.name.lower()
    slug = re.sub(r"[^a-z0-9_.-]", "-", name)
    return slug or "project"


def project_state_dir(project_dir: Path) -> Path:
    """~/.local/state/cog/<slug>/ respecting XDG_STATE_HOME."""
    base_str = os.environ.get("XDG_STATE_HOME")
    base = Path(base_str) if base_str else Path.home() / ".local" / "state"
    return base / "cog" / project_slug(project_dir)
