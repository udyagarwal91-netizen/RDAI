"""Skipper Packs social media content agent.

Folder paths used everywhere in the app live here, so there is one place
to change them. DATA_DIR and LOGS_DIR can be moved (for example to a
mounted disk on Railway) with environment variables of the same name.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BRANDS_DIR = ROOT / "brands"
# On Railway/Render, set BRANDS_DIR to a folder on the permanent disk (e.g. /data/brands) so settings you change
# in the Control Center survive new versions. The first start copies the brand folders there.
BRANDS_DIR = Path(os.environ.get("BRANDS_DIR", REPO_BRANDS_DIR))
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
LOGS_DIR = Path(os.environ.get("LOGS_DIR", ROOT / "logs"))
