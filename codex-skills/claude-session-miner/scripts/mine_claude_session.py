#!/usr/bin/env python
import sys
from pathlib import Path


def _bootstrap():
    repo_root = Path(__file__).resolve().parents[3]
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def main():
    _bootstrap()
    from conversation_search.core.session_miner import main as core_main

    core_main()


if __name__ == "__main__":
    main()
