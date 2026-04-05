# 代理池使用指南

## 为什么需要代理池？

在部署到海外服务器（如 Render）时，访问国内金融数据接口（东方财富、新浪等）可能遇到：
- 网络延迟高
- IP 被限制
- 连接不稳定

代理池通过多代理轮换、健康检查、自动切换等机制，显著提高接口稳定性。

## 快速开始

### 1. 启用代理池

```bash
# 环境变量方式
export USE_PROXY_POOL=true

# 或者在 .env 文件中设置
USE_PROXY_POOL=true
```

### 2. 配置代理

#### 方式一：环境变量配置（推荐）

```bash
# 单个代理
export PROXY_POOL='[{"http": "http://123.45.67.89:8080", "https": "http://123.45.67.89:8080", "name": "代理1"}]'

# 多个代理
export PROXY_POOL='[
  {"http": "http://proxy1.example.com:8080", "name": "代理1"},
  {"http": "http://proxy2.example.com:8080", "name": "代理2"},
  {"http": "http://proxy3.example.com:8080", "name": "代理3"}
]'
```

#### 方式二：传统单一代理（向后兼容）

```bash
export AKSHARE_PROXY=http://your-proxy:8080
```

### 3. 启动服务

```bash
python3 -m uvicorn main_optimized:app --host 0.0.0.0 --port 8080
```

## 代理池特性

### 1. 智能轮换
- 自动选择成功率高的代理
- 响应时间快的代理优先
- 随机选择避免单一代理过载

### 2. 健康检查
- 每5分钟自动检查代理健康状态
- 自动标记不健康代理
- 支持代理自动恢复

### 3. 故障切换
- 请求失败时自动切换代理
- 连续失败3次标记为不健康
- 所有代理失败时重置状态

### 4. 统计监控
- 记录每个代理的成功/失败次数
- 计算平均响应时间
- 提供 `/api/proxy-stats` 接口查看状态

## 查看代理池状态

访问以下接口查看代理池运行情况：

```bash
curl http://localhost:8080/api/proxy-stats
```

返回示例：

```json
{
  "enabled": true,
  "total": 3,
  "healthy": 2,
  "failed": 1,
  "proxies": [
    {
      "name": "代理1",
      "source": "env",
      "success_count": 150,
      "fail_count": 2,
      "avg_response_time": 1.23,
      "is_healthy": true
    },
    {
      "name": "代理2",
      "source": "env",
      "success_count": 120,
      "fail_count": 15,
      "avg_response_time": 2.56,
      "is_healthy": true
    },
    {
      "name": "代理3",
      "source": "env",
      "success_count": 50,
      "fail_count": 30,
      "avg_response_time": 5.67,
      "is_healthy": false
    }
  ]
}
```

## 推荐代理服务商

### 国内代理（适合海外服务器访问国内接口）

| 服务商 | 类型 | 价格 | 稳定性 | 推荐度 |
|--------|------|------|--------|--------|
| [快代理](https://www.kuaidaili.com/) | 付费 | ¥20/月起 | ⭐⭐⭐⭐⭐ | 强烈推荐 |
| [阿布云](https://www.abuyun.com/) | 付费 | ¥50/月起 | ⭐⭐⭐⭐⭐ | 强烈推荐 |
| [站大爷](http://www.zdaye.com/) | 付费/免费 | ¥10/月起 | ⭐⭐⭐⭐ | 推荐 |
| [芝麻代理](http://www.zhimaruanjian.com/) | 付费 | ¥30/月起 | ⭐⭐⭐⭐ | 推荐 |

### 免费代理（仅适合测试）

```bash
# 启用免费代理（稳定性较差，仅测试使用）
export USE_FREE_PROXIES=true
```

⚠️ **警告**：免费代理通常不稳定，且可能存在安全风险，生产环境不建议使用。

## Render 部署配置

在 `render.yaml` 中配置代理池：

```yaml
services:
  - type: web
    name: etf-nexus-monitor
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn main_optimized:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: USE_PROXY_POOL
        value: "true"
      - key: PROXY_POOL
        value: '[{"http": "http://your-proxy1:8080", "name": "代理1"}, {"http": "http://your-proxy2:8080", "name": "代理2"}]'
      - key: USE_MOCK
        value: "false"
```

## 最佳实践

### 1. 代理数量
- 建议配置 3-5 个代理
- 避免单点故障
- 分散请求压力

### 2. 代理质量
- 选择响应时间 < 2秒的代理
- 成功率 > 95% 的代理
- 定期更换代理IP

### 3. 监控告警
- 定期查看 `/api/proxy-stats`
- 监控代理健康状态
- 及时更换失效代理

### 4. 成本控制
- 开发测试：使用免费代理或Mock数据
- 生产环境：购买稳定付费代理
- 根据请求量选择合适套餐

## 故障排查

### 问题：代理池显示所有代理都不健康

**解决方案**：
1. 检查代理配置是否正确
2. 测试代理是否可用：`curl -x http://proxy:port http://quote.eastmoney.com/`
3. 查看日志确认具体错误

### 问题：请求仍然很慢

**解决方案**：
1. 选择地理位置更近的代理服务器
2. 增加代理数量分散压力
3. 使用更高质量的付费代理

### 问题：代理费用过高

**解决方案**：
1. 使用 Mock 数据模式（`USE_MOCK=true`）
2. 减少刷新频率（`REFRESH_MINUTES=5`）
3. 仅在市场交易时间启用实时数据

## 与限流策略的配合

代理池与原有的限流策略（熔断、指数退避等）是互补关系：

```
请求流程：
1. 检查熔断器状态
2. 获取可用代理
3. 执行请求（带重试）
4. 记录代理成功率
5. 限流控制（动态间隔）
```

两者结合可以最大程度保证数据抓取的稳定性和成功率。
