#!/usr/bin/env python3
"""
rebuild_cache.py — offline cache rebuilder for ETF NEXUS.

Usage:
    python3 rebuild_cache.py            # rebuild stats from existing kline files only
    python3 rebuild_cache.py --fetch    # also fetch fresh spot data from market API

The script imports helpers directly from main.py so it always stays in sync
with whatever compute_stats() currently produces.
"""
import sys
import os
import json
import argparse
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importing from main bootstraps the module-level globals (DATA_DIR, etc.)
from main import (
    SPOT_CACHE,
    KLINE_DIR,
    FEE_CACHE_FILE,
    BEIJING_TZ,
    logger,
    load_kline,
    compute_stats,
    _safe_float,
    _stats_is_complete,
    _REQUIRED_STATS_FIELDS,
)
from datetime import datetime
from pathlib import Path


def rebuild_stats_from_kline_files(force: bool = False) -> dict:
    """
    Scan all kline/*.json files and (re)compute stats.
    Returns a dict {code: stats}.
    If force=False, skips entries whose existing stats are already complete.
    """
    if not KLINE_DIR.exists():
        logger.warning("Kline dir not found: %s", KLINE_DIR)
        return {}

    files = list(KLINE_DIR.glob("*.json"))
    logger.info("Scanning %d kline files ...", len(files))

    # Load existing spot cache for current stats baseline
    existing_stats: dict = {}
    if SPOT_CACHE.exists():
        try:
            cached = json.loads(SPOT_CACHE.read_text(encoding="utf-8"))
            existing_stats = cached.get("stats") or {}
        except Exception as e:
            logger.warning("Could not load existing stats: %s", e)

    new_stats: dict = {}
    skipped = 0
    updated = 0

    for path in files:
        code = path.stem
        if not force and _stats_is_complete(existing_stats.get(code, {})):
            new_stats[code] = existing_stats[code]
            skipped += 1
            continue
        try:
            kline = load_kline(code)
            if len(kline) < 10:
                continue
            stats = compute_stats(kline)
            if stats:
                new_stats[code] = stats
                updated += 1
        except Exception as exc:
            logger.debug("Stats compute failed (%s): %s", code, exc)

    logger.info(
        "Stats rebuild done: updated=%d  skipped=%d  total=%d",
        updated, skipped, len(new_stats),
    )
    return new_stats


def main():
    parser = argparse.ArgumentParser(description="ETF NEXUS cache rebuilder")
    parser.add_argument(
        "--fetch", action="store_true",
        help="Also fetch fresh spot data from live market API",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force recompute stats even for ETFs that already have complete stats",
    )
    args = parser.parse_args()

    spot = {}
    indices = []

    if args.fetch:
        logger.info("Fetching live spot data ...")
        try:
            from main import fetch_spot_live, fetch_indices_live
            _provider, spot = fetch_spot_live({})
            logger.info("Spot fetched: %d ETFs via %s", len(spot), _provider)
            _iprovider, indices = fetch_indices_live()
        except Exception as exc:
            logger.error("Live fetch failed: %s", exc)
            sys.exit(1)
    else:
        # Load spot from existing cache
        if SPOT_CACHE.exists():
            try:
                cached = json.loads(SPOT_CACHE.read_text(encoding="utf-8"))
                spot = cached.get("spot") or {}
                indices = cached.get("indices") or []
                logger.info("Loaded spot from cache: %d ETFs", len(spot))
            except Exception as exc:
                logger.warning("Could not read spot cache: %s", exc)

    # Rebuild stats
    stats = rebuild_stats_from_kline_files(force=args.force)

    # Compute last_kline_update
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    last_kline_update = {code: today for code in stats}

    # Persist
    cache_data = {
        "spot": spot,
        "stats": stats,
        "indices": indices,
        "updated": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "live" if args.fetch else "cache",
        "provider": "rebuild",
        "last_kline_update": last_kline_update,
    }
    SPOT_CACHE.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Cache saved: %d ETFs / %d stats → %s",
        len(spot), len(stats), SPOT_CACHE,
    )


if __name__ == "__main__":
    main()
