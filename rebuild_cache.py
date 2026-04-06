#!/usr/bin/env python3
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import fetch_spot_akshare, fetch_indices_akshare, SPOT_CACHE, logger, BEIJING_TZ
from datetime import datetime

if __name__ == "__main__":
    logger.info("开始重建ETF行情缓存...")
    # 拉取行情
    spot = fetch_spot_akshare()
    if not spot:
        logger.error("拉取行情失败，退出")
        sys.exit(1)
    logger.info(f"拉取到 {len(spot)} 支ETF行情")
    # 拉取指数
    indices = fetch_indices_akshare()
    # 保存缓存
    cache_data = {
        "spot": spot,
        "stats": {},
        "indices": indices,
        "updated": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "live"
    }
    SPOT_CACHE.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
    logger.info(f"缓存重建完成，已保存到 {SPOT_CACHE}")
