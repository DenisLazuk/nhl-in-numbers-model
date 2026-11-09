#!/usr/bin/env python
"""Entry point: python scripts/nhl_etl_cli.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nhl_store.cli import main

if __name__ == "__main__":
    main()
