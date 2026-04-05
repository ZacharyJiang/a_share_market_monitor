"""
代理池系统 - 为AKShare提供稳定的代理支持
支持多代理源、健康检查、自动切换
"""
import os
import json
import time
import random
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path
import logging

logger = logging.getLogger("proxy-pool")

class ProxyPool:
    """代理池管理器"""
    
    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path(__file__).parent / "data"
        self.data_dir.mkdir(exist_ok=True)
        
        # 代理配置文件
        self.proxy_config_file = self.data_dir / "proxy_config.json"
        self.proxy_stats_file = self.data_dir / "proxy_stats.json"
        
        # 代理列表
        self.proxies: List[Dict] = []
        self.current_index = 0
        self.failed_proxies: set = set()
        
        # 统计信息
        self.stats: Dict[str, Dict] = {}
        
        # 健康检查间隔（秒）
        self.health_check_interval = 300  # 5分钟
        self.last_health_check = 0
        
        # 加载配置
        self._load_proxies()
        self._load_stats()
        
        # 初始化环境变量代理
        self._init_env_proxies()
    
    def _init_env_proxies(self):
        """从环境变量初始化代理"""
        # 支持多种环境变量格式
        proxy_vars = [
            "PROXY_POOL",  # JSON格式: [{"http": "http://ip:port", "https": "https://ip:port"}]
            "AKSHARE_PROXY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
        ]
        
        # 首先检查 PROXY_POOL（JSON格式）
        proxy_pool_json = os.environ.get("PROXY_POOL")
        if proxy_pool_json:
            try:
                pool = json.loads(proxy_pool_json)
                if isinstance(pool, list):
                    for p in pool:
                        self.add_proxy(p)
                    logger.info(f"Loaded {len(pool)} proxies from PROXY_POOL env")
            except json.JSONDecodeError:
                logger.warning("PROXY_POOL env var is not valid JSON")
        
        # 检查单一代理配置
        for var in ["AKSHARE_PROXY", "HTTP_PROXY", "HTTPS_PROXY"]:
            proxy_url = os.environ.get(var)
            if proxy_url and not any(p.get("http") == proxy_url for p in self.proxies):
                self.add_proxy({
                    "http": proxy_url,
                    "https": proxy_url.replace("http://", "https://") if "http://" in proxy_url else proxy_url,
                    "source": "env",
                    "name": var
                })
                logger.info(f"Loaded proxy from {var}")
    
    def add_proxy(self, proxy: Dict):
        """添加代理到池子"""
        proxy_id = proxy.get("http", "") or proxy.get("https", "")
        if not proxy_id:
            return
        
        # 检查是否已存在
        if any(p.get("http") == proxy_id for p in self.proxies):
            return
        
        proxy["id"] = f"proxy_{len(self.proxies)}"
        proxy["added_at"] = time.time()
        proxy["success_count"] = 0
        proxy["fail_count"] = 0
        proxy["last_used"] = 0
        proxy["avg_response_time"] = 0
        proxy["is_healthy"] = True
        
        self.proxies.append(proxy)
        logger.info(f"Added proxy: {proxy.get('name', proxy_id[:30])}...")
    
    def add_free_proxies(self):
        """添加免费代理源（谨慎使用，稳定性较差）"""
        # 这里可以集成免费代理API
        # 例如：快代理、站大爷、芝麻代理等
        free_proxies = []
        
        # 示例：从某个免费代理API获取
        try:
            # 注意：免费代理通常不稳定，建议仅作为备用
            response = requests.get(
                "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
                timeout=10
            )
            if response.status_code == 200:
                for line in response.text.strip().split("\n")[:5]:  # 只取前5个
                    ip_port = line.strip()
                    if ":" in ip_port:
                        free_proxies.append({
                            "http": f"http://{ip_port}",
                            "https": f"http://{ip_port}",
                            "source": "free_api",
                            "name": f"free_{ip_port}"
                        })
        except Exception as e:
            logger.warning(f"Failed to fetch free proxies: {e}")
        
        for proxy in free_proxies:
            self.add_proxy(proxy)
    
    def get_proxy(self) -> Optional[Dict]:
        """获取一个可用代理（轮询+权重）"""
        if not self.proxies:
            return None
        
        # 健康检查
        self._health_check()
        
        # 过滤掉失败的代理
        available = [p for p in self.proxies if p["id"] not in self.failed_proxies and p.get("is_healthy", True)]
        
        if not available:
            # 如果所有代理都失败了，重置失败列表
            logger.warning("All proxies failed, resetting failed list")
            self.failed_proxies.clear()
            available = self.proxies
        
        # 按成功率排序
        available.sort(key=lambda p: (
            p.get("fail_count", 0) / max(p.get("success_count", 1), 1),
            p.get("avg_response_time", 999)
        ))
        
        # 选择前3个中随机一个（避免总是用同一个）
        top_proxies = available[:3]
        selected = random.choice(top_proxies)
        
        selected["last_used"] = time.time()
        return {
            "http": selected.get("http"),
            "https": selected.get("https")
        }
    
    def report_success(self, proxy: Dict, response_time: float):
        """报告代理使用成功"""
        proxy_id = proxy.get("http", "")
        for p in self.proxies:
            if p.get("http") == proxy_id:
                p["success_count"] += 1
                # 更新平均响应时间
                old_avg = p.get("avg_response_time", 0)
                count = p["success_count"]
                p["avg_response_time"] = (old_avg * (count - 1) + response_time) / count
                p["is_healthy"] = True
                self._save_stats()
                break
    
    def report_failure(self, proxy: Dict, error: str = ""):
        """报告代理使用失败"""
        proxy_id = proxy.get("http", "")
        for p in self.proxies:
            if p.get("http") == proxy_id:
                p["fail_count"] += 1
                fail_rate = p["fail_count"] / max(p["success_count"] + p["fail_count"], 1)
                
                # 失败率超过50%或连续失败3次以上，标记为不健康
                if fail_rate > 0.5 or p["fail_count"] >= 3:
                    p["is_healthy"] = False
                    self.failed_proxies.add(p["id"])
                    logger.warning(f"Proxy {p.get('name', proxy_id[:30])} marked as unhealthy")
                
                self._save_stats()
                break
    
    def _health_check(self):
        """健康检查"""
        now = time.time()
        if now - self.last_health_check < self.health_check_interval:
            return
        
        self.last_health_check = now
        logger.info("Running proxy health check...")
        
        # 简单的健康检查：测试代理是否能访问东方财富
        test_url = "http://quote.eastmoney.com/"
        
        for proxy in self.proxies:
            if proxy.get("is_healthy"):
                continue  # 跳过健康的代理
            
            try:
                start = time.time()
                response = requests.get(
                    test_url,
                    proxies={"http": proxy["http"], "https": proxy["https"]},
                    timeout=10,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                if response.status_code == 200:
                    proxy["is_healthy"] = True
                    if proxy["id"] in self.failed_proxies:
                        self.failed_proxies.remove(proxy["id"])
                    logger.info(f"Proxy {proxy.get('name', proxy['id'])} is healthy again")
            except Exception:
                pass  # 仍然不健康
    
    def _load_proxies(self):
        """从文件加载代理配置"""
        if self.proxy_config_file.exists():
            try:
                with open(self.proxy_config_file, "r") as f:
                    config = json.load(f)
                    for p in config.get("proxies", []):
                        self.add_proxy(p)
            except Exception as e:
                logger.warning(f"Failed to load proxy config: {e}")
    
    def _save_proxies(self):
        """保存代理配置到文件"""
        try:
            with open(self.proxy_config_file, "w") as f:
                json.dump({"proxies": self.proxies}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save proxy config: {e}")
    
    def _load_stats(self):
        """加载统计信息"""
        if self.proxy_stats_file.exists():
            try:
                with open(self.proxy_stats_file, "r") as f:
                    self.stats = json.load(f)
            except Exception:
                pass
    
    def _save_stats(self):
        """保存统计信息"""
        try:
            stats = {
                "updated_at": time.time(),
                "proxies": [
                    {
                        "id": p["id"],
                        "name": p.get("name", ""),
                        "success_count": p.get("success_count", 0),
                        "fail_count": p.get("fail_count", 0),
                        "avg_response_time": p.get("avg_response_time", 0),
                        "is_healthy": p.get("is_healthy", True)
                    }
                    for p in self.proxies
                ]
            }
            with open(self.proxy_stats_file, "w") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save proxy stats: {e}")
    
    def get_stats(self) -> Dict:
        """获取代理池统计信息"""
        return {
            "total": len(self.proxies),
            "healthy": sum(1 for p in self.proxies if p.get("is_healthy", True)),
            "failed": len(self.failed_proxies),
            "proxies": [
                {
                    "name": p.get("name", p["id"]),
                    "source": p.get("source", "unknown"),
                    "success_count": p.get("success_count", 0),
                    "fail_count": p.get("fail_count", 0),
                    "avg_response_time": round(p.get("avg_response_time", 0), 2),
                    "is_healthy": p.get("is_healthy", True)
                }
                for p in self.proxies
            ]
        }


# 全局代理池实例
_proxy_pool: Optional[ProxyPool] = None

def get_proxy_pool() -> ProxyPool:
    """获取全局代理池实例"""
    global _proxy_pool
    if _proxy_pool is None:
        _proxy_pool = ProxyPool()
    return _proxy_pool


def configure_proxy_pool(data_dir: Path = None):
    """配置代理池"""
    global _proxy_pool
    _proxy_pool = ProxyPool(data_dir)
    return _proxy_pool
