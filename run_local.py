"""
run_local.py - Local Launcher & Automated Test Runner for Folivora

Usage:
    python run_local.py          # Starts Folivora server on http://localhost:8000
    python run_local.py --test   # Runs the full unit & integration test suite
"""

import argparse
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def run_tests() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(ROOT_DIR / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description="Folivora Local Runner")
    parser.add_argument("--test", action="store_true", help="Run automated unit tests")
    parser.add_argument("--port", type=int, default=8000, help="Port to serve on")
    args = parser.parse_args()

    if args.test:
        sys.exit(run_tests())

    import uvicorn
    uvicorn.run("src.app:app", host="127.0.0.1", port=args.port, reload=False)


if __name__ == "__main__":
    main()
