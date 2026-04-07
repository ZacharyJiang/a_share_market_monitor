#!/usr/bin/env python3
"""
独立监控采集进度，发现新完成的ETF K线就发送通知
不修改主程序，随时可以停止
"""

import json
import time
import os
import subprocess
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
KLINE_DIR = DATA_DIR / "kline"
STATE_FILE = Path(__file__).parent / "monitor-state.json"

def get_existing_kline_count():
    """获取当前已采集文件数量"""
    return len(list(KLINE_DIR.glob("*.json")))

def get_etf_name(code):
    """从spot_cache获取ETF名称"""
    spot_file = DATA_DIR / "spot_cache.json"
    if not spot_file.exists():
        return None
    try:
        data = json.load(open(spot_file, encoding="utf-8"))
        etf_spot = data.get("spot", {})
        if code in etf_spot:
            return etf_spot[code].get("name")
    except Exception:
        pass
    return None

def load_last_state():
    """加载上次状态"""
    if not STATE_FILE.exists():
        return {
            "last_count": 0,
            "notified_files": []
        }
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {
            "last_count": 0,
            "notified_files": []
        }

def save_state(state):
    """保存状态"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def send_notification(code, name=None):
    """发送通知"""
    script = Path(__file__).parent / "progress-notify.py"
    cmd = ["python3", str(script), code]
    if name:
        cmd.append(name)
    try:
        subprocess.run(cmd, capture_output=False, check=False)
    except Exception as e:
        print(f"Failed to send notification: {e}")

def main():
    print("Starting ETF collection progress monitor...")
    state = load_last_state()
    last_count = state.get("last_count", 0)
    notified_files = set(state.get("notified_files", []))
    
    print(f"Initial state: {last_count} files, {len(notified_files)} notified")
    
    while True:
        try:
            current_count = get_existing_kline_count()
            if current_count > last_count:
                # 有新增文件，找出新增的那个
                all_files = {p.stem for p in KLINE_DIR.glob("*.json")}
                new_files = all_files - notified_files
                
                for code in new_files:
                    if len(code) == 6:  # 只处理6位代码的ETF
                        name = get_etf_name(code)
                        print(f"New ETF collected: {code} {name or ''}")
                        send_notification(code, name)
                        notified_files.add(code)
                
                last_count = current_count
                state["last_count"] = last_count
                state["notified_files"] = list(notified_files)
                save_state(state)
            
            # 每分钟检查一次
            time.sleep(60)
            
        except KeyboardInterrupt:
            print("\nMonitor stopped by user")
            break
        except Exception as e:
            print(f"Error in monitor loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
