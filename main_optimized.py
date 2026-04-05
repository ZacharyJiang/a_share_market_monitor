"""
ETF NEXUS — A股全部场内ETF实时数据终端 Backend (Optimized Version)
AKShare + FastAPI + APScheduler
Architecture:
  - Spot refresh: every 1min via ak.fund_etf_spot_em() → ALL ETFs
  - Kline + stats: background thread gradually fetches for all ETFs
  - On-demand kline: /api/kline/:code fetches live if not cached

优化策略:
  1. 智能限流: 根据API响应动态调整请求间隔
  2. 指数退避: 遇到限流时自动增加间隔时间
  3. 优先级队列: 热门ETF优先更新，冷门延后
  4. 缓存策略: K线数据交易日只更新一次，非交易日不更新
  5. 批量控制: 减小批量大小，增加间隔时间
  6. 熔断机制: 连续失败时暂停请求一段时间
"""
import os, json, logging, threading, time, random
from datetime import datetime, timedelta, timezone
from collections import deque

BEIJING_TZ = timezone(timedelta(hours=8))
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("etf-nexus")

# ============================================================
# PROXY CONFIG — route AKShare (requests/urllib) through a China proxy
# ============================================================
from proxy_pool import get_proxy_pool, configure_proxy_pool

# 初始化代理池
proxy_pool = configure_proxy_pool(DATA_DIR if 'DATA_DIR' in locals() else None)

# 检查是否启用代理池
USE_PROXY_POOL = os.environ.get("USE_PROXY_POOL", "false").lower() == "true"

if USE_PROXY_POOL:
    logger.info("Proxy pool enabled")
    # 尝试添加免费代理（可选）
    if os.environ.get("USE_FREE_PROXIES", "false").lower() == "true":
        proxy_pool.add_free_proxies()
else:
    # 传统单一代理配置
    _PROXY = os.environ.get("AKSHARE_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if _PROXY:
        os.environ.setdefault("HTTP_PROXY", _PROXY)
        os.environ.setdefault("HTTPS_PROXY", _PROXY)
        logger.info(f"Single proxy configured: {_PROXY}")
    else:
        logger.info("No proxy configured — AKShare will connect directly")

# ============================================================
# CONFIG - 优化后的配置参数
# ============================================================
REFRESH_MINUTES = int(os.environ.get("REFRESH_MINUTES", "1"))
# 减小批量大小，避免触发限流
KLINE_BATCH_SIZE = int(os.environ.get("KLINE_BATCH_SIZE", "1"))
# 基础请求间隔（秒）
BASE_REQUEST_INTERVAL = float(os.environ.get("BASE_REQUEST_INTERVAL", "2.0"))
# 最大请求间隔（秒）
MAX_REQUEST_INTERVAL = float(os.environ.get("MAX_REQUEST_INTERVAL", "10.0"))
# 熔断阈值：连续失败多少次后暂停
CIRCUIT_BREAKER_THRESHOLD = int(os.environ.get("CIRCUIT_BREAKER_THRESHOLD", "5"))
# 熔断冷却时间（秒）
CIRCUIT_BREAKER_COOLDOWN = int(os.environ.get("CIRCUIT_BREAKER_COOLDOWN", "300"))
# K线数据刷新间隔（分钟）- 增加间隔
KLINE_REFRESH_MINUTES = int(os.environ.get("KLINE_REFRESH_MINUTES", "60"))

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
SPOT_CACHE = DATA_DIR / "spot_cache.json"
KLINE_DIR = DATA_DIR / "kline"
KLINE_DIR.mkdir(exist_ok=True)

# ============================================================
# IN-MEMORY STORE
# ============================================================
etf_spot = {}
etf_stats = {}
market_indices = []
last_updated = None
data_source = "none"
_lock = threading.Lock()

# 限流控制相关
_request_times = deque(maxlen=100)  # 记录最近100次请求时间
_current_interval = BASE_REQUEST_INTERVAL
_consecutive_failures = 0
_last_failure_time = None
_circuit_breaker_open = False
_circuit_breaker_opened_at = None

# 优先级队列 - 按规模排序的ETF代码列表
_priority_queue = []
_last_kline_update = {}  # 记录每个ETF的K线更新时间

DEFAULT_FEE = 0.20
KNOWN_FEES = {}

# ============================================================
# FEE DETAIL SYSTEM — fetch real fees from eastmoney, cache on disk
# ============================================================
FEE_CACHE_FILE = DATA_DIR / "fee_cache.json"
_fee_cache = {}
_fee_last_fetch = {}  # 记录每个费率的获取时间
_fee_fetch_interval = 86400  # 费率数据24小时内不重复获取


def _load_fee_cache():
    global _fee_cache
    if FEE_CACHE_FILE.exists():
        try:
            _fee_cache = json.loads(FEE_CACHE_FILE.read_text(encoding="utf-8"))
            logger.info(f"Fee cache loaded: {len(_fee_cache)} ETFs")
        except Exception:
            _fee_cache = {}


def _save_fee_cache():
    try:
        FEE_CACHE_FILE.write_text(json.dumps(_fee_cache, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"Save fee cache failed: {e}")


def _fetch_fee_from_eastmoney(code: str) -> dict:
    """Scrape real management/custody fee from eastmoney fund page."""
    import requests, re
    url = f"http://fundf10.eastmoney.com/jbgk_{code}.html"
    try:
        resp = requests.get(url, timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code != 200:
            return None
        text = resp.text
        result = {}
        m = re.search(r"管理费率[^%]*?(\d+\.\d+)%", text)
        if m:
            result["管理费"] = float(m.group(1))
        m = re.search(r"托管费率[^%]*?(\d+\.\d+)%", text)
        if m:
            result["托管费"] = float(m.group(1))
        return result if result else None
    except Exception:
        return None


def _should_fetch_fee(code: str) -> bool:
    """判断是否应该获取该ETF的费率数据"""
    if code in _fee_cache:
        last_fetch = _fee_last_fetch.get(code, 0)
        if time.time() - last_fetch < _fee_fetch_interval:
            return False
    return True


def refresh_fee_batch(codes: list):
    """Fetch real fee data for ETFs not yet cached. Runs in background with rate limiting."""
    global _fee_cache
    to_fetch = [c for c in codes if _should_fetch_fee(c)]
    if not to_fetch:
        logger.info("Fee cache already complete, nothing to fetch")
        return
    logger.info(f"Fee refresh: {len(to_fetch)} ETFs to fetch ({len(_fee_cache)} cached)")
    done = 0
    for i, code in enumerate(to_fetch):
        # 检查熔断状态
        if _is_circuit_breaker_open():
            logger.warning("Circuit breaker is open, pausing fee fetch")
            break
        try:
            _rate_limit_wait()
            fees = _fetch_fee_from_eastmoney(code)
            if fees:
                _fee_cache[code] = fees
                _fee_last_fetch[code] = time.time()
                done += 1
                _record_success()
            else:
                _record_failure()
        except Exception:
            _record_failure()
        # 增加间隔，避免被封
        if (i + 1) % 10 == 0:
            logger.info(f"Fee progress: {i + 1}/{len(to_fetch)} (found {done})")
            _save_fee_cache()
            time.sleep(1.0)
        else:
            time.sleep(0.5)
    _save_fee_cache()
    logger.info(f"Fee refresh complete: {done}/{len(to_fetch)} fetched, total cached: {len(_fee_cache)}")


def get_fee_detail(code: str, name: str = "") -> dict:
    """Return fee breakdown dict for an ETF from cache."""
    if code in _fee_cache:
        return _fee_cache[code]
    return {}


def format_fee_detail(detail: dict) -> str:
    """Format fee detail dict to display string."""
    if not detail:
        return ""
    parts = []
    for k, v in detail.items():
        parts.append(f"{k}{v:.2f}%")
    return ", ".join(parts)


# ============================================================
# RATE LIMITING & CIRCUIT BREAKER
# ============================================================
def _rate_limit_wait():
    """根据当前限流状态等待适当时间"""
    global _current_interval
    
    # 检查熔断状态
    if _is_circuit_breaker_open():
        raise Exception("Circuit breaker is open")
    
    # 计算需要等待的时间
    now = time.time()
    if _request_times:
        last_request = _request_times[-1]
        elapsed = now - last_request
        if elapsed < _current_interval:
            sleep_time = _current_interval - elapsed
            time.sleep(sleep_time)
    
    _request_times.append(time.time())


def _record_success():
    """记录成功请求，逐渐降低间隔"""
    global _current_interval, _consecutive_failures
    _consecutive_failures = 0
    # 逐渐降低间隔，但不低于基础值
    _current_interval = max(BASE_REQUEST_INTERVAL, _current_interval * 0.95)


def _record_failure():
    """记录失败请求，增加间隔并检查熔断"""
    global _current_interval, _consecutive_failures, _circuit_breaker_open, _circuit_breaker_opened_at, _last_failure_time
    _consecutive_failures += 1
    _last_failure_time = time.time()
    
    # 指数退避增加间隔
    _current_interval = min(MAX_REQUEST_INTERVAL, _current_interval * 1.5)
    
    # 检查是否达到熔断阈值
    if _consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
        _circuit_breaker_open = True
        _circuit_breaker_opened_at = time.time()
        logger.warning(f"Circuit breaker opened! Will pause for {CIRCUIT_BREAKER_COOLDOWN}s")


def _is_circuit_breaker_open() -> bool:
    """检查熔断器是否打开"""
    global _circuit_breaker_open, _circuit_breaker_opened_at
    
    if not _circuit_breaker_open:
        return False
    
    # 检查是否已过冷却期
    if _circuit_breaker_opened_at and time.time() - _circuit_breaker_opened_at > CIRCUIT_BREAKER_COOLDOWN:
        _circuit_breaker_open = False
        _circuit_breaker_opened_at = None
        global _consecutive_failures, _current_interval
        _consecutive_failures = 0
        _current_interval = BASE_REQUEST_INTERVAL
        logger.info("Circuit breaker closed, resuming normal operations")
        return False
    
    return True


def _get_priority_codes() -> list:
    """获取按优先级排序的ETF代码列表（规模大的优先）"""
    global _priority_queue
    if not _priority_queue:
        with _lock:
            # 按规模降序排序
            sorted_etfs = sorted(etf_spot.items(), key=lambda x: x[1].get('scale', 0), reverse=True)
            _priority_queue = [code for code, _ in sorted_etfs]
    return _priority_queue


# ============================================================
# AKSHARE FETCHERS (with rate limiting)
# ============================================================
def _safe_float(val, default=0):
    """Safely convert to float, return default on failure."""
    try:
        v = float(val) if val is not None and str(val).strip() not in ("", "-", "nan", "None") else default
        return v
    except (ValueError, TypeError):
        return default


def _get_proxy_for_request():
    """获取用于请求的代理"""
    if USE_PROXY_POOL and proxy_pool:
        return proxy_pool.get_proxy()
    return None


def _apply_proxy(proxy: dict):
    """应用代理到环境变量"""
    if proxy:
        os.environ["HTTP_PROXY"] = proxy.get("http", "")
        os.environ["HTTPS_PROXY"] = proxy.get("https", "")


def _clear_proxy():
    """清除代理环境变量"""
    if "HTTP_PROXY" in os.environ:
        del os.environ["HTTP_PROXY"]
    if "HTTPS_PROXY" in os.environ:
        del os.environ["HTTPS_PROXY"]


def fetch_spot_akshare():
    """Fetch ALL ETF spot data via AKShare. Returns dict {code: row_dict}."""
    import akshare as ak
    
    # 检查熔断状态
    if _is_circuit_breaker_open():
        logger.warning("Circuit breaker is open, skipping spot fetch")
        return {}
    
    logger.info("AKShare: fetching spot data for all ETFs...")
    
    # 获取代理
    current_proxy = _get_proxy_for_request()
    if current_proxy:
        _apply_proxy(current_proxy)
        logger.info(f"Using proxy: {current_proxy.get('http', '')[:30]}...")
    
    start_time = time.time()
    
    for retry in range(3):
        try:
            _rate_limit_wait()
            df = ak.fund_etf_spot_em()
            if df is not None and not df.empty:
                _record_success()
                if current_proxy and USE_PROXY_POOL:
                    proxy_pool.report_success(current_proxy, time.time() - start_time)
                break
            logger.warning(f"AKShare spot fetch returned empty, retry {retry+1}/3...")
            _record_failure()
            # 切换代理重试
            if USE_PROXY_POOL and retry < 2:
                current_proxy = _get_proxy_for_request()
                if current_proxy:
                    _apply_proxy(current_proxy)
                    logger.info(f"Switching to proxy: {current_proxy.get('http', '')[:30]}...")
            time.sleep(_current_interval * 2)
        except Exception as e:
            logger.warning(f"AKShare spot fetch failed (retry {retry+1}/3): {e}")
            _record_failure()
            if current_proxy and USE_PROXY_POOL:
                proxy_pool.report_failure(current_proxy, str(e))
            # 切换代理重试
            if USE_PROXY_POOL and retry < 2:
                current_proxy = _get_proxy_for_request()
                if current_proxy:
                    _apply_proxy(current_proxy)
                    logger.info(f"Switching to proxy: {current_proxy.get('http', '')[:30]}...")
            time.sleep(_current_interval * 2)
    else:
        logger.error("AKShare spot fetch failed after 3 retries")
        return {}
    
    cols = list(df.columns)
    logger.info(f"AKShare spot columns: {cols}")
    df["代码"] = df["代码"].astype(str).str.zfill(6)

    def _find_col(candidates, df_cols):
        for c in candidates:
            if c in df_cols:
                return c
        return None

    col_price = _find_col(["最新价", "现价", "收盘价"], cols)
    col_name = _find_col(["名称", "基金名称"], cols)
    col_chg = _find_col(["涨跌幅", "涨幅"], cols)
    col_vol = _find_col(["成交量"], cols)
    col_amt = _find_col(["成交额"], cols)
    col_scale = _find_col(["总市值", "市值", "基金规模"], cols)
    col_open = _find_col(["开盘价", "开盘", "今开"], cols)
    col_high = _find_col(["最高价", "最高"], cols)
    col_low = _find_col(["最低价", "最低"], cols)
    col_prev = _find_col(["昨收", "昨收价"], cols)
    col_iopv = _find_col(["IOPV实时估值", "IOPV", "估值"], cols)
    col_shares = _find_col(["最新份额", "份额"], cols)

    logger.info(f"Column mapping: price={col_price}, name={col_name}, chg={col_chg}, "
                f"vol={col_vol}, amt={col_amt}, scale={col_scale}, iopv={col_iopv}, shares={col_shares}")

    result = {}
    for _, row in df.iterrows():
        code = row["代码"]
        try:
            price = _safe_float(row.get(col_price) if col_price else None)
            if price <= 0:
                continue
            iopv = _safe_float(row.get(col_iopv) if col_iopv else None)
            shares = _safe_float(row.get(col_shares) if col_shares else None)
            if iopv > 0 and shares > 0:
                scale = round(iopv * shares / 1e8, 2)
            else:
                scale_raw = _safe_float(row.get(col_scale) if col_scale else None)
                if scale_raw > 1e6:
                    scale = round(scale_raw / 1e8, 2)
                elif scale_raw > 0:
                    scale = round(scale_raw, 2)
                else:
                    amt = _safe_float(row.get(col_amt) if col_amt else None)
                    scale = round(amt / 1e8, 2) if amt > 0 else 0

            name_str = str(row.get(col_name, "") if col_name else "")
            fee_detail = get_fee_detail(code, name_str)
            fee_total = round(sum(fee_detail.values()), 2) if fee_detail else None

            result[code] = {
                "code": code,
                "name": name_str,
                "currentPrice": round(price, 4),
                "chgPct": round(_safe_float(row.get(col_chg) if col_chg else None), 2),
                "scale": scale,
                "volume": int(_safe_float(row.get(col_vol) if col_vol else None)),
                "turnover": round(_safe_float(row.get(col_amt) if col_amt else None) / 1e8, 2),
                "fee": fee_total,
                "feeDetail": format_fee_detail(fee_detail),
                "open": round(_safe_float(row.get(col_open) if col_open else None), 4),
                "high": round(_safe_float(row.get(col_high) if col_high else None), 4),
                "low": round(_safe_float(row.get(col_low) if col_low else None), 4),
                "prevClose": round(_safe_float(row.get(col_prev) if col_prev else None), 4),
            }
        except (ValueError, TypeError) as e:
            logger.warning(f"Spot parse error for {code}: {e}")
            continue
    logger.info(f"AKShare: spot data fetched for {len(result)} ETFs")
    return result


def fetch_indices_akshare():
    """Fetch major index quotes via multiple AKShare APIs (fallback chain)."""
    import akshare as ak
    
    # 检查熔断状态
    if _is_circuit_breaker_open():
        logger.warning("Circuit breaker is open, using cached/mock indices")
        return _mock_indices()
    
    target = {"000001": "上证指数", "399001": "深证成指", "000300": "沪深300"}
    indices = []

    # Method 1: stock_zh_index_spot_em
    method1_map = {}
    try:
        _rate_limit_wait()
        df = ak.stock_zh_index_spot_em()
        _record_success()
        if df is not None and not df.empty:
            cols = list(df.columns)
            logger.info(f"Index spot columns (method1): {cols}")
            col_code = next((c for c in ["代码", "序号"] if c in cols), None)
            col_price = next((c for c in ["最新价", "现价", "收盘"] if c in cols), None)
            col_chg = next((c for c in ["涨跌幅", "涨幅"] if c in cols), None)
            if col_code and col_price:
                df[col_code] = df[col_code].astype(str)
                for code, name in target.items():
                    match = df[df[col_code] == code]
                    if not match.empty:
                        row = match.iloc[0]
                        method1_map[code] = {
                            "name": name,
                            "val": round(_safe_float(row.get(col_price)), 2),
                            "chg": round(_safe_float(row.get(col_chg) if col_chg else None), 2),
                        }
            logger.info(f"Method1 found {len(method1_map)}/{len(target)} indices")
    except Exception as e:
        logger.warning(f"Index method1 (stock_zh_index_spot_em) failed: {e}")
        _record_failure()

    # Method 2: Use index daily kline for any missing indices
    missing = {c: n for c, n in target.items() if c not in method1_map}
    method2_map = {}
    if missing and not _is_circuit_breaker_open():
        for code, name in missing.items():
            try:
                _rate_limit_wait()
                sym = f"sh{code}" if code.startswith("000") else f"sz{code}"
                df = ak.stock_zh_index_daily(symbol=sym)
                _record_success()
                if df is not None and not df.empty:
                    last = df.iloc[-1]
                    close_col = next((c for c in ["close", "收盘"] if c in df.columns), df.columns[3] if len(df.columns) > 3 else None)
                    if close_col:
                        close = _safe_float(last.get(close_col))
                        prev = _safe_float(df.iloc[-2].get(close_col)) if len(df) > 1 else close
                        chg = round((close - prev) / prev * 100, 2) if prev > 0 else 0
                        method2_map[code] = {"name": name, "val": round(close, 2), "chg": chg}
            except Exception as e:
                logger.warning(f"Index method2 ({code}) failed: {e}")
                _record_failure()
                break  # 失败后停止尝试其他指数
        logger.info(f"Method2 filled {len(method2_map)}/{len(missing)} missing indices")

    # Combine: method1 + method2, in target order
    combined = {**method1_map, **method2_map}
    for code in target:
        if code in combined:
            indices.append(combined[code])

    if indices:
        logger.info(f"Indices fetched: {len(indices)} total")
        return indices

    # Fallback to mock
    logger.warning("All index methods failed, using mock indices")
    return _mock_indices()


def fetch_kline_akshare(code: str, days: int = 1200) -> list:
    """Fetch daily kline for one ETF via AKShare with rate limiting."""
    import akshare as ak
    
    # 检查熔断状态
    if _is_circuit_breaker_open():
        logger.warning(f"Circuit breaker is open, skipping kline fetch for {code}")
        return []
    
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    
    for retry in range(2):
        try:
            _rate_limit_wait()
            hist = ak.fund_etf_hist_em(
                symbol=code, period="daily",
                start_date=start_date, end_date=end_date, adjust="qfq"
            )
            if hist is not None and not hist.empty:
                _record_success()
                break
            logger.warning(f"Kline {code} fetch returned empty, retry {retry+1}/2...")
            _record_failure()
            time.sleep(_current_interval)
        except Exception as e:
            logger.warning(f"Kline {code} fetch failed (retry {retry+1}/2): {e}")
            _record_failure()
            time.sleep(_current_interval)
    else:
        logger.error(f"Kline {code} fetch failed after 2 retries")
        return []
    
    if hist is None or hist.empty:
        return []
    
    cols = list(hist.columns)
    col_date = next((c for c in ["日期", "date"] if c in cols), cols[0])
    col_open = next((c for c in ["开盘", "开盘价", "open"] if c in cols), cols[1] if len(cols) > 1 else None)
    col_close = next((c for c in ["收盘", "收盘价", "close"] if c in cols), cols[2] if len(cols) > 2 else None)
    col_high = next((c for c in ["最高", "最高价", "high"] if c in cols), cols[3] if len(cols) > 3 else None)
    col_low = next((c for c in ["最低", "最低价", "low"] if c in cols), cols[4] if len(cols) > 4 else None)
    col_vol = next((c for c in ["成交量", "volume"] if c in cols), cols[5] if len(cols) > 5 else None)
    
    kline = []
    for _, r in hist.iterrows():
        try:
            kline.append({
                "date": str(r[col_date])[:10],
                "open": round(_safe_float(r.get(col_open)), 4),
                "close": round(_safe_float(r.get(col_close)), 4),
                "high": round(_safe_float(r.get(col_high)), 4),
                "low": round(_safe_float(r.get(col_low)), 4),
                "volume": int(_safe_float(r.get(col_vol))),
            })
        except (ValueError, TypeError):
            continue
    return kline


# ============================================================
# STATS COMPUTATION
# ============================================================
def _max_drawdown(arr):
    if not arr or len(arr) < 2:
        return 0
    peak = arr[0]
    mdd = 0
    for v in arr:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < mdd:
            mdd = dd
    return round(mdd * 100, 2)


def compute_stats(kline: list) -> dict:
    """Compute stats from kline data."""
    if not kline or len(kline) < 10:
        return {}
    closes = [k["close"] for k in kline]
    current = closes[-1]
    all_high = max(closes)
    all_low = min(closes)
    one_year = closes[-250:] if len(closes) > 250 else closes
    three_year = closes[-750:] if len(closes) > 750 else closes
    return {
        "dropFromHigh": round((current - all_high) / all_high * 100, 2),
        "riseFromLow": round((current - all_low) / all_low * 100, 2),
        "maxDD1Y": _max_drawdown(one_year),
        "maxDD3Y": _max_drawdown(three_year),
        "sparkline": [round(c, 4) for c in closes[-60:]],
    }


# ============================================================
# KLINE CACHE (disk-based, one file per ETF)
# ============================================================
def _kline_path(code: str) -> Path:
    return KLINE_DIR / f"{code}.json"


def save_kline(code: str, kline: list):
    try:
        _kline_path(code).write_text(json.dumps(kline, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"Save kline {code} failed: {e}")


def load_kline(code: str) -> list:
    p = _kline_path(code)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def should_update_kline(code: str) -> bool:
    """判断是否应该更新该ETF的K线数据"""
    global _last_kline_update
    
    # 非交易日不更新
    if not is_trading_day():
        return False
    
    last_update = _last_kline_update.get(code)
    if not last_update:
        return True
    
    # 同一交易日内只更新一次
    now = datetime.now(BEIJING_TZ)
    last = datetime.fromtimestamp(last_update, BEIJING_TZ)
    
    if now.date() != last.date():
        return True
    
    return False


# ============================================================
# SPOT CACHE (disk)
# ============================================================
def save_spot_cache():
    try:
        SPOT_CACHE.write_text(json.dumps({
            "spot": {c: s for c, s in etf_spot.items()},
            "stats": {c: s for c, s in etf_stats.items()},
            "indices": market_indices,
            "updated": last_updated,
            "source": data_source,
            "last_kline_update": _last_kline_update,
        }, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"Save spot cache failed: {e}")


def load_spot_cache() -> bool:
    global etf_spot, etf_stats, market_indices, last_updated, data_source, _last_kline_update
    if SPOT_CACHE.exists():
        try:
            d = json.loads(SPOT_CACHE.read_text(encoding="utf-8"))
            etf_spot = d.get("spot", {})
            etf_stats = d.get("stats", {})
            market_indices = d.get("indices", [])
            last_updated = d.get("updated")
            data_source = d.get("source", "cache")
            _last_kline_update = d.get("last_kline_update", {})
            logger.info(f"Cache loaded: {len(etf_spot)} ETFs, source={data_source}")
            return bool(etf_spot)
        except Exception:
            pass
    return False


# ============================================================
# MOCK FALLBACK
# ============================================================
def _mock_indices():
    return [
        {"name": "上证指数", "val": 3287.45, "chg": 0.82},
        {"name": "深证成指", "val": 10456.78, "chg": -0.35},
        {"name": "沪深300", "val": 3850.00, "chg": 0.45},
    ]

_MOCK_ETFS = [
    ("510300","沪深300ETF",4.1,1282),("510500","中证500ETF",6.8,685),
    ("588000","科创50ETF",1.05,412),("159915","创业板ETF",2.6,356),
    ("510050","上证50ETF",2.9,789),("512100","中证1000ETF",1.45,198),
    ("513100","纳指ETF",1.78,320),("159934","黄金ETF",5.2,156),
    ("512010","医药ETF",0.52,245),("515030","新能源ETF",1.12,178),
    ("512660","军工ETF",1.08,267),("512880","证券ETF",0.95,312),
    ("515790","光伏ETF",0.68,89),("512690","酒ETF",1.32,145),
    ("159869","游戏ETF",0.88,56),("512480","半导体ETF",1.55,398),
    ("513050","中概互联ETF",0.72,210),("512200","房地产ETF",0.62,42),
    ("159766","旅游ETF",0.81,34),("562340","中证A50ETF",1.02,168),
    ("513130","恒生科技ETF",0.68,175),("518880","金ETF",6.1,220),
    ("511010","国债ETF",120.5,95),("159941","纳斯达克ETF",2.35,88),
    ("510330","华夏沪深300",4.85,456),
]

def generate_mock():
    global etf_spot, etf_stats, market_indices, last_updated, data_source, _priority_queue
    logger.info("Generating mock data...")
    for code, name, base, scale in _MOCK_ETFS:
        rand = random.Random(int(code) + 42)
        price = base * (0.6 + rand.random() * 0.5)
        kline = []
        d = datetime(2023, 1, 3)
        for _ in range(1100):
            while d.weekday() >= 5:
                d += timedelta(days=1)
            vol = 0.015 + rand.random() * 0.02
            drift = (rand.random() - 0.48) * 0.003
            change = price * (drift + vol * (rand.random() - 0.5) * 2)
            op = price; cl = max(0.01, price + change)
            hi = max(op, cl) * (1 + rand.random() * 0.008)
            lo = min(op, cl) * (1 - rand.random() * 0.008)
            volume = int((50 + rand.random() * 200) * scale * 0.1)
            kline.append({"date": d.strftime("%Y-%m-%d"),
                "open": round(op, 4), "close": round(cl, 4),
                "high": round(hi, 4), "low": round(lo, 4), "volume": volume})
            price = cl; d += timedelta(days=1)
        save_kline(code, kline)
        stats = compute_stats(kline)
        fee_detail = get_fee_detail(code, name)
        fee_total = round(sum(fee_detail.values()), 2)
        etf_spot[code] = {
            "code": code, "name": name, "currentPrice": round(price, 4),
            "scale": scale, "fee": fee_total,
            "feeDetail": format_fee_detail(fee_detail),
            "pe": round(10 + rand.random() * 30, 2),
            "chgPct": round((rand.random() - 0.5) * 4, 2), "volume": 0, "turnover": 0,
        }
        etf_stats[code] = stats
    market_indices = _mock_indices()
    last_updated = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    data_source = "mock"
    _priority_queue = []  # 重置优先级队列
    save_spot_cache()
    logger.info(f"Mock data: {len(etf_spot)} ETFs")


# ============================================================
# 交易日历缓存
# ============================================================
_trading_dates = set()
_last_trading_date_update = None

def update_trading_calendar():
    """更新A股交易日历，每年更新一次即可"""
    global _trading_dates, _last_trading_date_update
    now = datetime.now(BEIJING_TZ)
    if _last_trading_date_update and (now - _last_trading_date_update).days < 30:
        return
    
    # 检查熔断状态
    if _is_circuit_breaker_open():
        logger.warning("Circuit breaker is open, skipping trading calendar update")
        return
    
    try:
        import akshare as ak
        year = now.year
        _rate_limit_wait()
        df = ak.tool_trade_date_hist_sina()
        _record_success()
        df["trade_date"] = df["trade_date"].astype(str)
        _trading_dates = set(df[df["trade_date"].str.startswith(str(year)) | df["trade_date"].str.startswith(str(year-1))]["trade_date"].tolist())
        _last_trading_date_update = now
        logger.info(f"交易日历更新完成，共缓存 {len(_trading_dates)} 个交易日")
    except Exception as e:
        logger.warning(f"交易日历更新失败: {e}")
        _record_failure()

def is_trading_day() -> bool:
    """判断今天是否为A股交易日"""
    now = datetime.now(BEIJING_TZ)
    today_str = now.strftime("%Y%m%d")
    if now.weekday() >= 5:
        return False
    if _trading_dates:
        return today_str in _trading_dates
    return True

def is_trading_time() -> bool:
    """判断当前是否为A股交易时间：交易日 9:30-11:30, 13:00-15:00"""
    if not is_trading_day():
        return False
    now = datetime.now(BEIJING_TZ)
    hour = now.hour
    minute = now.minute
    current = hour * 100 + minute
    return (930 <= current <= 1130) or (1300 <= current <= 1500)


# ============================================================
# REFRESH JOBS (Optimized)
# ============================================================
def refresh_spot():
    """Fast refresh: spot data + indices for ALL ETFs. Runs every 1 min (only in trading time)."""
    global etf_spot, market_indices, last_updated, data_source, _priority_queue
    
    # 允许通过环境变量强制刷新，即使在非交易时间
    force_refresh = os.environ.get("FORCE_REFRESH", "false").lower() == "true"
    
    if not is_trading_time() and not force_refresh:
        logger.debug("非交易时间，跳过行情更新")
        return
    
    try:
        new_spot = fetch_spot_akshare()
        if not new_spot:
            logger.warning("Spot refresh returned empty, keeping cached data")
            return
        
        with _lock:
            for code, info in new_spot.items():
                if code in etf_spot:
                    etf_spot[code].update(info)
                else:
                    etf_spot[code] = info
            for code in list(etf_spot.keys()):
                if code not in new_spot and data_source != "mock":
                    del etf_spot[code]
            # 重置优先级队列，让下次K线更新使用新的排序
            _priority_queue = []
        
        try:
            indices = fetch_indices_akshare()
            if indices:
                market_indices = indices
        except Exception:
            pass
        
        last_updated = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        data_source = "live"
        save_spot_cache()
        logger.info(f"Spot refreshed: {len(etf_spot)} ETFs")
    except Exception as e:
        logger.error(f"Spot refresh failed: {e}")


def refresh_kline_batch():
    """Slow background: fetch kline + compute stats in batches with smart rate limiting."""
    global etf_stats, _last_kline_update
    
    # 非交易日跳过K线更新
    if not is_trading_day():
        logger.debug("非交易日，跳过K线更新")
        return
    
    # 检查熔断状态
    if _is_circuit_breaker_open():
        logger.warning("Circuit breaker is open, skipping kline refresh")
        return
    
    # 使用优先级队列（按规模排序）
    codes = _get_priority_codes()
    total = len(codes)
    done = 0
    skipped = 0
    
    logger.info(f"Starting kline refresh for {total} ETFs (batch size: {KLINE_BATCH_SIZE})")
    
    for i in range(0, total, KLINE_BATCH_SIZE):
        # 检查熔断状态
        if _is_circuit_breaker_open():
            logger.warning("Circuit breaker opened during kline refresh, stopping")
            break
        
        batch = codes[i:i + KLINE_BATCH_SIZE]
        for code in batch:
            # 检查是否需要更新
            if not should_update_kline(code):
                skipped += 1
                continue
            
            try:
                kline = fetch_kline_akshare(code)
                if kline and len(kline) >= 10:
                    save_kline(code, kline)
                    stats = compute_stats(kline)
                    with _lock:
                        etf_stats[code] = stats
                    _last_kline_update[code] = time.time()
                    done += 1
            except Exception as e:
                logger.error(f"Kline {code}: {e}")
                # 如果遇到错误，检查是否应该熔断
                if _is_circuit_breaker_open():
                    break
        
        progress = min(i + KLINE_BATCH_SIZE, total)
        logger.info(f"Kline progress: {progress}/{total} (done: {done}, skipped: {skipped}, interval: {_current_interval:.2f}s)")
        
        # 增加间隔，避免被封
        time.sleep(_current_interval)
    
    save_spot_cache()
    logger.info(f"Kline refresh complete: {done}/{total} updated, {skipped} skipped")


# ============================================================
# FASTAPI APP
# ============================================================
scheduler = BackgroundScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_fee_cache()
    loaded = load_spot_cache()
    
    # 启动时尝试获取实时数据
    use_mock = os.environ.get("USE_MOCK", "false").lower() == "true"
    
    if use_mock:
        logger.info("Startup: USE_MOCK is set, generating mock data...")
        generate_mock()
    else:
        try:
            logger.info("Startup: attempting live AKShare refresh...")
            refresh_spot()
            logger.info(f"Startup: live refresh succeeded, source={data_source}, {len(etf_spot)} ETFs")
        except Exception as e:
            logger.error(f"Startup: live refresh failed: {e}")
        
        # 如果没有获取到数据，生成Mock数据
        if not etf_spot:
            logger.info("No ETF data available, generating mock data...")
            generate_mock()
    
    # 更新交易日历
    update_trading_calendar()
    
    # Spot refresh every N minutes (只在交易时间执行)
    scheduler.add_job(refresh_spot, "interval", minutes=REFRESH_MINUTES,
                      id="spot_refresh", max_instances=1, coalesce=True)
    
    # Kline refresh every KLINE_REFRESH_MINUTES minutes (优化后的间隔)
    scheduler.add_job(refresh_kline_batch, "interval", minutes=KLINE_REFRESH_MINUTES,
                      id="kline_refresh", max_instances=1, coalesce=True)
    
    # Fee refresh daily (fees rarely change)
    scheduler.add_job(lambda: refresh_fee_batch(list(etf_spot.keys())),
                      "interval", hours=24, id="fee_refresh", max_instances=1, coalesce=True)
    
    # 交易日历更新 (每月一次)
    scheduler.add_job(update_trading_calendar, "interval", days=30,
                      id="calendar_refresh", max_instances=1, coalesce=True)
    
    scheduler.start()
    logger.info(f"Scheduler started: spot every {REFRESH_MINUTES}min, kline every {KLINE_REFRESH_MINUTES}min")
    
    # Trigger initial kline fetch in background thread
    threading.Thread(target=refresh_kline_batch, daemon=True).start()
    
    # Trigger fee fetch in background thread
    threading.Thread(target=lambda: refresh_fee_batch(list(etf_spot.keys())), daemon=True).start()
    
    yield
    scheduler.shutdown(wait=False)

app = FastAPI(title="ETF NEXUS (Optimized)", lifespan=lifespan)


@app.get("/api/etf-data")
async def get_etf_data():
    """Return all ETF data (spot + stats, no full kline)."""
    with _lock:
        etfs = []
        for code, spot in etf_spot.items():
            entry = {**spot}
            stats = etf_stats.get(code, {})
            entry["dropFromHigh"] = stats.get("dropFromHigh", None)
            entry["riseFromLow"] = stats.get("riseFromLow", None)
            entry["maxDD1Y"] = stats.get("maxDD1Y", None)
            entry["maxDD3Y"] = stats.get("maxDD3Y", None)
            entry["sparkline"] = stats.get("sparkline", [])
            fee_detail = get_fee_detail(code)
            if fee_detail:
                entry["fee"] = round(sum(fee_detail.values()), 2)
                entry["feeDetail"] = format_fee_detail(fee_detail)
            entry.setdefault("feeDetail", "")
            etfs.append(entry)
    return JSONResponse({
        "etfs": etfs,
        "indices": market_indices,
        "updated": last_updated,
        "source": data_source,
    })


@app.get("/api/kline/{code}")
async def get_kline(code: str, range: str = "1Y"):
    """Return kline data. Tries cache first, then fetches live."""
    kline = load_kline(code)
    if not kline:
        try:
            kline = fetch_kline_akshare(code)
            if kline:
                save_kline(code, kline)
                stats = compute_stats(kline)
                with _lock:
                    etf_stats[code] = stats
        except Exception as e:
            logger.error(f"On-demand kline {code}: {e}")
    if not kline:
        return JSONResponse({"error": "Kline data not available"}, status_code=404)
    range_map = {"1M": 22, "3M": 66, "6M": 132, "1Y": 250, "3Y": 750, "全部": 999999}
    n = range_map.get(range, 250)
    sliced = kline[-min(n, len(kline)):]
    spot = etf_spot.get(code, {})
    return JSONResponse({
        "code": code,
        "name": spot.get("name", code),
        "fee": spot.get("fee", DEFAULT_FEE),
        "scale": spot.get("scale", 0),
        "kline": sliced,
    })


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "etf_count": len(etf_spot),
        "stats_count": len(etf_stats),
        "source": data_source,
        "updated": last_updated,
        "refresh_minutes": REFRESH_MINUTES,
        "kline_refresh_minutes": KLINE_REFRESH_MINUTES,
        "circuit_breaker": "open" if _circuit_breaker_open else "closed",
        "current_interval": round(_current_interval, 2),
        "consecutive_failures": _consecutive_failures,
        "proxy": _PROXY or "none",
    }


@app.get("/api/diag")
async def diag():
    """Quick diagnostic: report current state without making new AKShare calls."""
    sample = []
    with _lock:
        for i, (code, spot) in enumerate(etf_spot.items()):
            if i >= 3:
                break
            sample.append({**spot, "stats": etf_stats.get(code, {})})
    
    # 代理池状态
    proxy_stats = {}
    if USE_PROXY_POOL and proxy_pool:
        proxy_stats = proxy_pool.get_stats()
    
    return {
        "current_source": data_source,
        "current_etf_count": len(etf_spot),
        "current_stats_count": len(etf_stats),
        "indices": market_indices,
        "updated": last_updated,
        "circuit_breaker": "open" if _circuit_breaker_open else "closed",
        "current_interval": round(_current_interval, 2),
        "consecutive_failures": _consecutive_failures,
        "proxy": _PROXY or "none",
        "use_proxy_pool": USE_PROXY_POOL,
        "proxy_pool_stats": proxy_stats,
        "sample_etfs": sample,
    }


@app.get("/api/proxy-stats")
async def proxy_stats():
    """获取代理池统计信息"""
    if not USE_PROXY_POOL or not proxy_pool:
        return JSONResponse({
            "enabled": False,
            "message": "Proxy pool is not enabled. Set USE_PROXY_POOL=true to enable."
        })
    
    return {
        "enabled": True,
        **proxy_pool.get_stats()
    }


# Serve static files
static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
