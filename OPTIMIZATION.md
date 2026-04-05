# ETF NEXUS 优化版 - API限流优化说明

## 优化策略概览

针对AKShare API调用频率过高可能被封禁的问题，实施了以下优化策略：

### 1. 智能限流系统
- **动态请求间隔**: 根据API响应情况自动调整请求间隔
- **指数退避机制**: 遇到错误时自动增加间隔时间（1.5倍），成功时逐渐降低（0.95倍）
- **请求时间记录**: 使用deque记录最近100次请求时间，确保合理间隔

### 2. 熔断器模式 (Circuit Breaker)
- **熔断阈值**: 连续5次失败后自动开启熔断
- **冷却时间**: 熔断后暂停300秒（5分钟）
- **自动恢复**: 冷却期后自动关闭熔断器，恢复正常请求

### 3. 优先级队列
- **按规模排序**: ETF按规模（scale）降序排列，热门ETF优先更新
- **智能跳过**: 同一交易日内已更新的ETF自动跳过

### 4. 缓存策略优化
- **交易日判断**: 非交易日不更新K线数据
- **日内去重**: 同一交易日只更新一次K线
- **费率缓存**: 费率数据24小时内不重复获取

### 5. 批量控制
- **减小批量大小**: 从2减小到1，逐个获取K线
- **增加间隔**: 基础间隔2秒，最大10秒
- **K线刷新间隔**: 从30分钟增加到60分钟

### 6. 代理池系统 (Proxy Pool) ⭐ 新增
- **多代理轮换**: 支持配置多个代理，自动选择最优代理
- **健康检查**: 每5分钟自动检查代理可用性
- **故障切换**: 请求失败自动切换代理，记录成功率
- **统计监控**: 提供 `/api/proxy-stats` 接口查看代理状态

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| REFRESH_MINUTES | 1 | 行情刷新间隔（分钟） |
| KLINE_REFRESH_MINUTES | 60 | K线刷新间隔（分钟） |
| KLINE_BATCH_SIZE | 1 | K线批量大小 |
| BASE_REQUEST_INTERVAL | 2.0 | 基础请求间隔（秒） |
| MAX_REQUEST_INTERVAL | 10.0 | 最大请求间隔（秒） |
| CIRCUIT_BREAKER_THRESHOLD | 5 | 熔断阈值（连续失败次数） |
| CIRCUIT_BREAKER_COOLDOWN | 300 | 熔断冷却时间（秒） |

## 环境变量配置

创建 `.env` 文件：

```bash
# 行情刷新间隔（分钟）
REFRESH_MINUTES=1

# K线数据刷新间隔（分钟）- 建议60分钟以上
KLINE_REFRESH_MINUTES=60

# K线批量大小 - 减小到1，避免触发限流
KLINE_BATCH_SIZE=1

# 基础请求间隔（秒）
BASE_REQUEST_INTERVAL=2.0

# 最大请求间隔（秒）
MAX_REQUEST_INTERVAL=10.0

# 熔断阈值
CIRCUIT_BREAKER_THRESHOLD=5

# 熔断冷却时间（秒）
CIRCUIT_BREAKER_COOLDOWN=300

# 强制使用Mock数据（用于测试）
USE_MOCK=false

# 强制刷新（非交易时间也刷新）
FORCE_REFRESH=false

# 代理设置（单代理模式）
# AKSHARE_PROXY=http://your-proxy:8080

# 代理池配置（多代理模式，推荐用于海外服务器）
# USE_PROXY_POOL=true
# PROXY_POOL=[{"http": "http://proxy1:8080", "name": "代理1"}, {"http": "http://proxy2:8080", "name": "代理2"}]
```

详细的代理池配置请参考 [PROXY_POOL_GUIDE.md](PROXY_POOL_GUIDE.md)

## 运行方式

### 使用优化版（正常模式）
```bash
python3 -m uvicorn main_optimized:app --host 0.0.0.0 --port 8080
```

### 使用Mock数据（演示/测试）
```bash
USE_MOCK=true python3 -m uvicorn main_optimized:app --host 0.0.0.0 --port 8080
```

### 后台运行
```bash
export USE_MOCK=true
nohup python3 -m uvicorn main_optimized:app --host 0.0.0.0 --port 8080 > server.log 2>&1 &
```

### 查看日志
```bash
tail -f server.log
```

## API端点

- `GET /` - 前端页面
- `GET /api/etf-data` - 获取所有ETF数据
- `GET /api/kline/{code}` - 获取指定ETF的K线数据
- `GET /api/health` - 健康检查（包含限流状态）
- `GET /api/diag` - 诊断信息

## 健康检查响应示例

```json
{
  "status": "ok",
  "etf_count": 1000,
  "stats_count": 1000,
  "source": "live",
  "updated": "2026-04-05 10:30:00",
  "refresh_minutes": 1,
  "kline_refresh_minutes": 60,
  "circuit_breaker": "closed",
  "current_interval": 2.5,
  "consecutive_failures": 0,
  "proxy": "none"
}
```

## 注意事项

1. **交易时间**: 只在A股交易时间（9:30-11:30, 13:00-15:00）自动获取实时数据
2. **非交易时间**: 使用缓存数据或Mock数据
3. **熔断状态**: 可通过 `/api/health` 查看熔断器状态
4. **日志监控**: 建议定期查看日志，监控API调用情况

## 与原版的对比

| 特性 | 原版 | 优化版 |
|------|------|--------|
| K线批量大小 | 2 | 1 |
| K线刷新间隔 | 30分钟 | 60分钟 |
| 请求间隔 | 固定3秒 | 动态2-10秒 |
| 熔断机制 | 无 | 有 |
| 优先级队列 | 无 | 有（按规模） |
| 限流控制 | 无 | 有（指数退避） |
| 代理池 | 无 | 有（多代理轮换） |
| 交易时间判断 | 有 | 有（更完善） |

## 监控建议

1. 定期查看 `/api/health` 检查熔断器状态
2. 监控 `current_interval` 值，如果持续很高说明API压力大
3. 关注 `consecutive_failures` 计数，避免频繁触发熔断
4. 查看服务器日志了解详细的API调用情况
