"""Application-level constants and filesystem locations.

Everything the app persists lives under one workspace directory so the whole
suite can be backed up, synced or moved by copying a single folder.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "Digital Assets Studio"
APP_ID = "digital-assets-studio"
APP_VERSION = "0.7.1"
TAGLINE = "One suite. Every digital asset."

# Aptabase ingest key for anonymous usage analytics. It is a write-only key and
# is meant to ship inside the binary; it can neither read the dashboard nor
# identify anyone. Left blank, the app sends nothing at all - which is what a
# build from source does unless DAS_APTABASE_KEY is set.
APTABASE_APP_KEY = "A-US-6846957150"

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def _default_workspace() -> Path:
    """Pick a sensible per-OS home for projects and settings."""
    override = os.environ.get("DAS_HOME") or os.environ.get("AIPATH_STUDIO_HOME")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "DigitalAssetsStudio"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Digital Assets Studio"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "digital-assets-studio"


def _legacy_workspaces() -> list[Path]:
    """Where the app kept its data under its previous name.

    These strings are load-bearing: they are how an existing install finds its
    projects after the rename. Do not remove them."""
    out: list[Path] = []
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        out.append(Path(base) / "AIpathStudio")
    elif sys.platform == "darwin":
        out.append(Path.home() / "Library" / "Application Support" / "AIpath Studio")
    out.append(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aipath-studio")
    return out


WORKSPACE = _default_workspace()
PROJECTS_DIR = WORKSPACE / "projects"
SETTINGS_FILE = WORKSPACE / "settings.json"
LOG_FILE = WORKSPACE / "studio.log"
CACHE_DIR = WORKSPACE / "cache"

PKG_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PKG_DIR / "assets"


def migrate_legacy_workspace() -> Path | None:
    """Carry projects and settings over from the old AIpath Studio folder.

    Renaming the app must not cost anyone their work, so the first run under the
    new name moves the old workspace across rather than starting empty. The old
    folder is left behind, renamed, so nothing is destroyed if this goes wrong.
    """
    if WORKSPACE.exists() and any(WORKSPACE.iterdir()):
        return None
    for old in _legacy_workspaces():
        if not old.exists() or not any(old.iterdir()):
            continue
        try:
            WORKSPACE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(old, WORKSPACE, dirs_exist_ok=True)
            marker = old.with_name(old.name + " (migrated)")
            if not marker.exists():
                old.rename(marker)
            return old
        except Exception:  # noqa: BLE001
            log.exception("could not migrate the old workspace at %s", old)
    return None


def ensure_dirs() -> None:
    migrate_legacy_workspace()
    for p in (WORKSPACE, PROJECTS_DIR, CACHE_DIR):
        p.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# LLM roles - each job in the suite routes to its own provider/model pairing so
# cheap work can run on a cheap (or local) model and hard work on a strong one.
# --------------------------------------------------------------------------

ROLE_PLANNING = "planning"
ROLE_RESEARCH = "research"
ROLE_LONGFORM = "longform"
ROLE_EDITING = "editing"
ROLE_MARKETING = "marketing"
ROLE_METADATA = "metadata"
ROLE_SCRIPT = "script"
ROLE_IMAGE_PROMPT = "image_prompt"
ROLE_IMAGE = "image"
ROLE_CODE = "code"

TEXT_ROLES: list[tuple[str, str, str]] = [
    (ROLE_PLANNING, "Planning & outlines", "Structure, chapter maps, episode beats, step lists."),
    (ROLE_RESEARCH, "Research & fact-checking", "Market angles, competitor scans, the adversarial second pass."),
    (ROLE_LONGFORM, "Long-form drafting", "Chapter prose, scripts, long documents. Your most expensive tokens live here."),
    (ROLE_EDITING, "Line editing & continuity", "Tightening, voice consistency, continuity sweeps."),
    (ROLE_MARKETING, "Marketing copy", "Blurbs, store listings, hooks, titles, ad copy."),
    (ROLE_METADATA, "Metadata & keywords", "Categories, keywords, tags, filenames, alt text. Cheap model territory."),
    (ROLE_SCRIPT, "Video scripts", "Narration, hooks, captions, chapter markers."),
    (ROLE_IMAGE_PROMPT, "Image prompting", "Turning a scene brief into a prompt an image model will honour."),
    (ROLE_CODE, "Code & data", "Build scripts, JSON transforms, spreadsheet formulas."),
]

IMAGE_ROLES: list[tuple[str, str, str]] = [
    (ROLE_IMAGE, "Image generation", "Covers, panels, thumbnails, screenshots, banners."),
]

ALL_ROLES = TEXT_ROLES + IMAGE_ROLES
