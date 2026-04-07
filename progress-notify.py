#!/usr/bin/env python3
"""
ETF 采集进度通知
每完成一只ETF采集，发送飞书通知给用户
"""

import json
import os
import sys
import math
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
KLINE_DIR = DATA_DIR / "kline"

def get_progress():
    """获取当前采集进度"""
    # 已采集的K线文件数量
    kline_files = list(KLINE_DIR.glob("*.json"))
    collected = len(kline_files)
    
    # 读取spot_cache获取总ETF数量
    total = 0
    spot_file = DATA_DIR / "spot_cache.json"
    if spot_file.exists():
        try:
            data = json.load(open(spot_file, encoding="utf-8"))
            etf_spot = data.get("spot", {})
            total = len(etf_spot)
        except Exception as e:
            print(f"Warning: read spot_cache failed: {e}")
    
    if total == 0:
        total = 1422  # 实际已确认大约1422只
    
    remaining = total - collected
    percent = (collected / total * 100) if total > 0 else 0
    
    return {
        "collected": collected,
        "total": total,
        "remaining": remaining,
        "percent": round(percent, 2)
    }

def generate_message(code, name=None):
    """生成通知消息"""
    prog = get_progress()
    name_str = f"({name})" if name else ""
    
    msg = f"✅ 已完成ETF采集: {code} {name_str}\n\n"
    msg += f"📊 当前进度: {prog['collected']} / {prog['total']} ({prog['percent']}%)\n"
    msg += f"⏳ 剩余: {prog['remaining']} 只\n"
    
    # 预计剩余时间 (按每天16只计算)
    if prog['remaining'] > 0:
        days_remaining = math.ceil(prog['remaining'] / 16)
        msg += f"⌛ 预计还需要: {days_remaining} 天\n"
    
    return msg

def send_notification(message):
    """通过openclaw gateway API发送飞书通知"""
    import requests
    
    # 读取OPENCLAW_TOKEN
    if os.path.exists("/root/.openclaw/workspace_coder/ai-newsletter/.env"):
        with open("/root/.openclaw/workspace_coder/ai-newsletter/.env") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    if key.strip() == "OPENCLAW_TOKEN":
                        os.environ[key.strip()] = value.strip()
    
    token = os.environ.get("OPENCLAW_TOKEN")
    if not token:
        print("ERROR: OPENCLAW_TOKEN not found")
        return False
    
    url = "http://127.0.0.1:10845/api/v1/send-text"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": "feishu",
        "target": "ou_67da0b7029564a121bf82791fb433864",
        "text": message
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        print("Notification sent successfully")
        return True
    except Exception as e:
        print(f"Failed to send notification: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python progress-notify.py <code> [name]")
        sys.exit(1)
    
    code = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) >= 3 else None
    
    msg = generate_message(code, name)
    print(msg)
    send_notification(msg)
