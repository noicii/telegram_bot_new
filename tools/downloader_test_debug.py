#!/usr/bin/env python3
"""Verbose wrapper for the production downloader diagnostic harness.

This is a diagnostic tool only. The supplied URL is a sample, never a target
for source-specific downloader logic. Any production fix derived from a test
must be generic and must be validated with additional links.

The wrapper enables INFO-level downloader logging so failures can be correlated
with the production downloader phase without modifying production code.
"""
from __future__ import annotations

import logging
import sys

from downloader_test import parse_args, main


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[DOWNLOADER] %(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    raise SystemExit(__import__("asyncio").run(main(parse_args())))
