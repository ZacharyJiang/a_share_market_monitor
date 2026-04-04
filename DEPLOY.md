# ETF NEXUS 部署指南

## 部署到 Render（推荐）

### 一键部署
点击下方按钮直接部署到 Render：

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ZacharyJiang/a_share_market_monitor)

### 手动部署步骤

1. **Fork 或克隆仓库**
   ```bash
   git clone https://github.com/ZacharyJiang/a_share_market_monitor.git
   ```

2. **修改代码**
   - 使用 `main_optimized.py` 作为主程序（已优化API限流）
   - 确保 `render.yaml` 配置正确

3. **推送到 GitHub**
   ```bash
   git add .
   git commit -m "Add optimized version with rate limiting"
   git push origin main
   ```

4. **在 Render 创建服务**
   - 登录 [Render](https://render.com)
   - 点击 "New" → "Web Service"
   - 连接你的 GitHub 仓库
   - 选择 "Python 3" 环境
   - 构建命令：`pip install -r requirements.txt`
   - 启动命令：`uvicorn main_optimized:app --host 0.0.0.0 --port $PORT`
   - 点击 "Create Web Service"

5. **等待部署完成**
   - Render 会自动构建和部署
   - 部署完成后会提供一个公网 URL

## 环境变量配置

在 Render 的 Environment 页面设置以下变量：

| 变量名 | 值 | 说明 |
|--------|-----|------|
| USE_MOCK | true | 使用模拟数据（演示用） |
| REFRESH_MINUTES | 1 | 行情刷新间隔（分钟） |
| KLINE_REFRESH_MINUTES | 60 | K线刷新间隔（分钟） |
| KLINE_BATCH_SIZE | 1 | K线批量大小 |
| BASE_REQUEST_INTERVAL | 2.0 | 基础请求间隔（秒） |
| MAX_REQUEST_INTERVAL | 10.0 | 最大请求间隔（秒） |
| CIRCUIT_BREAKER_THRESHOLD | 5 | 熔断阈值 |
| CIRCUIT_BREAKER_COOLDOWN | 300 | 熔断冷却时间（秒） |

## 访问网站

部署完成后，Render 会提供一个类似 `https://etf-nexus-monitor.onrender.com` 的 URL，直接在浏览器中访问即可。

## 注意事项

1. **免费计划限制**：Render 免费计划有以下限制：
   - 服务在15分钟无访问后会进入休眠
   - 首次访问需要等待服务启动（约30秒）
   - 每月有750小时的免费额度

2. **数据持久化**：免费计划的磁盘不是持久化的，重启后数据会丢失。建议：
   - 使用 Mock 数据进行演示
   - 或升级到付费计划获得持久化磁盘

3. **API 限流**：优化版已内置限流机制，避免被封禁

## 本地测试

```bash
# 安装依赖
pip install -r requirements.txt

# 运行优化版（使用Mock数据）
USE_MOCK=true python3 -m uvicorn main_optimized:app --host 0.0.0.0 --port 8080

# 访问 http://localhost:8080
```
