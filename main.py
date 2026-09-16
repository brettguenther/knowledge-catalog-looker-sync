"""CLI entrypoint for looker-kc-sync."""

import sys
from pathlib import Path

# Add src to sys.path for direct execution
src_dir = str(Path(__file__).parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from looker_kc_sync.cli import main

if __name__ == "__main__":
    main()
