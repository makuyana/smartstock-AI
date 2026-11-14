#!/usr/bin/env python3
"""Load the four demo businesses. Safe to run more than once."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import normalize_database_url
from app.demo.seed import seed_demo


def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL is missing")
    print(seed_demo(normalize_database_url(url)))


if __name__ == "__main__":
    main()
