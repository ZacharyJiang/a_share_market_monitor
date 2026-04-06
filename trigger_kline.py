#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import refresh_kline_batch, logger, load_spot_cache

if __name__ == "__main__":
    logger.info("手动触发K线批量处理...")
    # 先加载缓存
    loaded = load_spot_cache()
    logger.info(f"加载缓存完成: {loaded}, 共{len(sys.modules['main'].etf_spot)}支ETF")
    refresh_kline_batch()
    logger.info("K线批量处理完成")
