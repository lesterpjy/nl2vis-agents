"""What each deterministic check actually did over one benchmark run, crossed with correctness.

The reduction lives in `data_agents.metrics` because the product reduces its Session Store with the same code; this is
the benchmark's call into it, where `correct` is known.
"""

import argparse
import json
from pathlib import Path

from data_agents.system.metrics import report


def main() -> None:
    parser = argparse.ArgumentParser(description="Reduce a benchmark run's Turn streams to one row per deterministic check.")
    parser.add_argument("results", type=Path)
    print(report(json.loads(parser.parse_args().results.read_text())))


if __name__ == "__main__":
    main()
