"""Compatibility wrapper for the safe all-account warmup runner.

Deprecated: the original implementation launched one thread per account and
ignored proxy-group exclusivity, which is unsafe for this pipeline.
Use `run_all_warmup.py` directly (or `run_scheduler.py`) for the maintained flow.
"""

import argparse
import logging
import os
import runpy
import sys


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
logger = logging.getLogger("warmup_all_wrapper")


def main():
    parser = argparse.ArgumentParser(
        description="Deprecated wrapper that delegates to run_all_warmup.py"
    )
    parser.add_argument("--minutes", type=int, default=30, help="Deprecated (ignored)")
    parser.add_argument("--max-comments", type=int, default=5, help="Deprecated (ignored)")
    args = parser.parse_args()

    logger.warning(
        "run_warmup_all.py is deprecated and unsafe; delegating to run_all_warmup.py "
        "(proxy-group sequential runner)."
    )
    if args.minutes != 30 or args.max_comments != 5:
        logger.warning(
            "Custom --minutes/--max-comments are not supported by the safe runner and were ignored."
        )

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(repo_dir, "run_all_warmup.py")
    if not os.path.exists(target):
        logger.error(f"Missing target runner: {target}")
        sys.exit(1)

    runpy.run_path(target, run_name="__main__")


if __name__ == "__main__":
    main()
